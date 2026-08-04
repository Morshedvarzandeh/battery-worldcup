"""Feed completed sales back into battery-data.

This is the reason the market is worth building at all, beyond the transaction.

Today the used-part values in the datasets are *estimates*: benchmarked from
marketplace listings, carrying ``evidence='estimated'`` and a confidence around
0.6. They are the weakest numbers in the whole valuation and the ones that move
the parts-out and reuse pathways the most.

A completed sale is not an estimate. It is an observation of what one identified
pack, of known health and known age, actually fetched on a known date. Enough of
those and the weakest input becomes the strongest -- and the valuation that
priced the pack rests on prices the market itself set.

The loop:

    scan -> valuation -> listing -> sale -> observation -> battery-data
                ^                                              |
                +----------------------------------------------+

Sales are written as ``replacement_price`` rows against the pack's product
revision, with ``evidence='distributor_listing'`` and the sale date as the
validity start. They are never written as ``component_market_value``: that table
prices a *module*, and a whole-pack sale says nothing about what one module out
of it is worth.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from .models import Listing, ListingStatus

#: Sales below this count are not aggregated into a price. One transaction is
#: an anecdote; it may have been a favour, a fire sale or a mistake.
MINIMUM_SALES_FOR_A_PRICE = 3

SOURCE_UID = "src/bv-market-sales"
PROVENANCE_NOTE = SOURCE_UID


def _quote(value: object) -> str:
    """SQL literal, or NULL."""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def summarise(listings: list[Listing]) -> dict[str, dict]:
    """Aggregate completed sales by pack model.

    Returns:
        ``{pack_model_key: {...}}`` with the median price per kWh, the sample
        size, the health range the sales covered and the date span. Median
        rather than mean, because a thin market produces outliers and one
        desperate seller should not move the published figure.
    """
    by_model: dict[str, list[Listing]] = defaultdict(list)
    for listing in listings:
        if listing.status is not ListingStatus.SOLD or not listing.pack_model_key:
            continue
        if listing.sold_price is None or listing.rated_kwh <= 0:
            continue
        by_model[listing.pack_model_key].append(listing)

    summary: dict[str, dict] = {}
    for key, sales in by_model.items():
        if len(sales) < MINIMUM_SALES_FOR_A_PRICE:
            continue
        per_kwh = sorted(sale.sold_price / sale.rated_kwh for sale in sales)
        middle = len(per_kwh) // 2
        median = (
            per_kwh[middle]
            if len(per_kwh) % 2
            else (per_kwh[middle - 1] + per_kwh[middle]) / 2
        )
        healths = [sale.state_of_health for sale in sales]
        dates = [sale.sold_at.date() for sale in sales if sale.sold_at]
        summary[key] = {
            "pack_model_key": key,
            "label": sales[0].battery_label,
            "currency": sales[0].currency,
            "sample_size": len(sales),
            "median_price_per_kwh": round(median, 2),
            "lowest_price_per_kwh": round(per_kwh[0], 2),
            "highest_price_per_kwh": round(per_kwh[-1], 2),
            "mean_soh": round(sum(healths) / len(healths), 4),
            "min_soh": round(min(healths), 4),
            "max_soh": round(max(healths), 4),
            "first_sale": min(dates).isoformat() if dates else None,
            "last_sale": max(dates).isoformat() if dates else None,
        }
    return summary


def to_battery_data_sql(
    listings: list[Listing], *, region: str = "EU", manufacturer_slug: dict[str, str] | None = None
) -> str:
    """Render aggregated sales as battery-data ``replacement_price`` rows.

    The output is applied the same way ``tools/export_to_battery_data.py``
    output is: reviewed, then loaded. Nothing here writes to a database
    directly, because a price nobody looked at is exactly the kind of claim
    battery-data exists to keep out.

    Args:
        listings: Sold listings. Anything else is ignored.
        region: Region the sales took place in.
        manufacturer_slug: Optional pack-model key to manufacturer slug, so the
            product uid can be built. Falls back to matching on the alias.
    """
    summary = summarise(listings)
    if not summary:
        return (
            "-- No pack model has reached "
            f"{MINIMUM_SALES_FOR_A_PRICE} completed sales yet.\n"
            "-- One transaction is an anecdote, not a price.\n"
        )

    slugs = manufacturer_slug or {}
    lines = [
        "-- " + "=" * 69,
        "-- battery-value marketplace: observed sale prices",
        "--",
        "-- Each row is the median of completed private sales of one pack",
        "-- model, in EUR per kWh of nameplate energy. These are observations",
        "-- of what packs actually fetched, not estimates of what they should,",
        "-- which is what separates them from the seeded used-part values they",
        f"-- are meant to replace. Minimum sample size {MINIMUM_SALES_FOR_A_PRICE}.",
        "--",
        "-- The health range each figure covers is in the notes: a price per",
        "-- kWh means nothing without knowing how worn the packs behind it",
        "-- were.",
        "-- " + "=" * 69,
        "",
        "INSERT INTO source (uid, kind, title, url, license, redistributable,",
        "                    retrieved_at, scope_note)",
        f"VALUES ({_quote(SOURCE_UID)},'distributor_listing',",
        "        'battery-value marketplace completed sales',",
        "        'https://github.com/Morshedvarzandeh/battery-worldcup',",
        "        'AGPL-3.0-or-later', true, now(),",
        "        'Private-treaty sales of identified packs whose state of health "
        "was independently assessed from a battery passport at listing time. "
        "Thin market; sample sizes are small and stated.')",
        "ON CONFLICT (uid) DO NOTHING;",
        "",
        "INSERT INTO source_location (source_id, locator_kind)",
        f"SELECT id,'dataset' FROM source WHERE uid={_quote(SOURCE_UID)}",
        "ON CONFLICT DO NOTHING;",
        "",
        "INSERT INTO provenance (source_location_id, evidence, extraction,",
        "                        confidence, review, derivation_note)",
        "SELECT sl.id, 'distributor_listing'::evidence_class,",
        "       'manual_entry'::extraction_method, 0.75,",
        f"       'pending_review'::review_state, {_quote(PROVENANCE_NOTE)}",
        "  FROM source_location sl JOIN source s ON s.id=sl.source_id",
        f" WHERE s.uid={_quote(SOURCE_UID)} LIMIT 1;",
        "",
    ]

    for key in sorted(summary):
        entry = summary[key]
        slug = slugs.get(key)
        uid = f"pack/{slug}/{key}" if slug else None
        note = (
            f"median of {entry['sample_size']} completed private sales, "
            f"{entry['first_sale']} to {entry['last_sale']}; "
            f"state of health {entry['min_soh']:.0%}-{entry['max_soh']:.0%}, "
            f"range {entry['lowest_price_per_kwh']:.0f}-"
            f"{entry['highest_price_per_kwh']:.0f} per kWh"
        )
        lines.append(f"-- {entry['label']}")
        lines.append(
            "INSERT INTO replacement_price (product_revision_id, price_per_kwh,\n"
            "       currency, includes_labour, valid_from, region, provenance_id,\n"
            "       notes)\n"
            "SELECT r.id, "
            f"{entry['median_price_per_kwh']},{_quote(entry['currency'])},false,\n"
            f"       {_quote(entry['last_sale'] or date.today().isoformat())},"
            f"{_quote(region)},\n"
            "       (SELECT id FROM provenance WHERE derivation_note="
            f"{_quote(PROVENANCE_NOTE)}),\n"
            f"       {_quote(note)}\n"
            "  FROM product_revision r JOIN product p ON p.id = r.product_id\n"
            + (
                f" WHERE p.uid={_quote(uid)};"
                if uid
                else f" WHERE p.uid LIKE {_quote(f'%/{key}')};"
            )
        )
        lines.append("")

    return "\n".join(lines)


__all__ = [
    "MINIMUM_SALES_FOR_A_PRICE",
    "SOURCE_UID",
    "summarise",
    "to_battery_data_sql",
]
