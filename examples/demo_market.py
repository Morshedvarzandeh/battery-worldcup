"""Populate a demo market, end to end, from the example passports.

Runs the whole thing the way it is meant to be used: scan a passport, get a
valuation with a quotable reference, list the pack from that reference, take
offers, close some sales, and watch completed sales become observations that
can go back into battery-data.

    python examples/demo_market.py
    bv serve            # then open http://127.0.0.1:8000/market

Everything is written to the stores that ``BV_STORE_PATH`` and
``BV_MARKET_PATH`` point at, so a demo never lands in a real one by accident.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("BV_STORE_PATH", str(ROOT / ".demo" / "valuations.sqlite3"))
os.environ.setdefault("BV_MARKET_PATH", str(ROOT / ".demo" / "market.sqlite3"))

from battery_value.market.resolver import build_resolver  # noqa: E402
from battery_value.marketplace import MarketError, MarketService  # noqa: E402
from battery_value.marketplace.observations import summarise  # noqa: E402
from battery_value.passport.resolver import PassportResolver  # noqa: E402
from battery_value.serialisation import valuation_to_dict  # noqa: E402
from battery_value.store import default_store  # noqa: E402
from battery_value.valuation.engine import ValuationEngine  # noqa: E402

# Sellers with packs, as they would actually turn up: a garage clearing a
# workshop, a leasing company retiring a fleet, an owner with one battery.
SELLERS = [
    ("nl-ev-garage", "NL, Rotterdam"),
    ("de-fleet-remarketing", "DE, Koln"),
    ("fr-independent", "FR, Lyon"),
    ("dk-solar-installer", "DK, Aarhus"),
]

# Packs to put on the market: the example passports, plus variations that give
# the market something to compare. Health is what moves the price, so the same
# model appears at several states of health on purpose.
PACKS = [
    ("Nissan", "Leaf ZE1 40 kWh", "2019-04-01", 40, "NMC532", 81, 850),
    ("Nissan", "Leaf ZE1 40 kWh", "2018-09-01", 40, "NMC532", 74, 1400),
    ("Nissan", "Leaf ZE1 40 kWh", "2020-06-01", 40, "NMC532", 88, 420),
    ("Nissan", "Leaf ZE0 24 kWh", "2013-05-01", 24, "LMO", 66, 1600),
    ("Renault", "Zoe ZE40 41 kWh", "2018-03-01", 41, "NMC622", 84, 700),
    ("BMW", "i3 94Ah (33 kWh)", "2017-07-01", 33.2, "NMC111", 86, 900),
    ("Tesla", "Model 3 Long Range (75 kWh)", "2019-02-01", 75, "NCA", 90, 620),
    ("Hyundai", "Kona Electric 64 kWh", "2020-01-01", 64, "NMC622", 92, 500),
    ("BYD", "Atto 3 Blade (60 kWh)", "2022-05-01", 60.5, "LFP", 94, 700),
    ("Volkswagen", "ID.3 Pro (58 kWh)", "2021-03-01", 58, "NMC712", 89, 560),
]

# Buyers, and what they are each after. A second-life integrator and a repair
# shop want different things from the same pile of batteries.
BUYERS = [
    ("secondlife-storage-nl", 0.92),
    ("ev-repair-hamburg", 1.02),
    ("modules-for-diy", 0.78),
]


def passport_document(maker, model, made, kwh, chemistry, soh, cycles):
    """An EU digital battery passport, as a scanner would hand one over."""
    return {
        "batteryPassport": {
            "generalInformation": {
                "batteryPassportId": f"urn:demo:{model.lower().replace(' ', '-')}-{made}",
                "manufacturerName": maker,
                "vehicleModel": f"{maker} {model}",
                "manufacturingDate": made,
                "batteryCategory": "EV battery",
            },
            "performanceAndDurability": {
                "ratedCapacity": {"value": kwh, "unit": "kWh"},
                "stateOfHealth": {"value": soh, "unit": "%"},
                "numberOfFullCycles": cycles,
                "measurementDate": (date.today() - timedelta(days=14)).isoformat(),
            },
            "materialComposition": {"batteryChemistry": chemistry},
            "circularity": {"safetyFlags": []},
        }
    }


def main() -> int:
    store = default_store()
    service = MarketService()
    engine = ValuationEngine(prices=build_resolver(offline=True))
    passports = PassportResolver()

    print("scanning and valuing\n" + "-" * 62)
    listings = []
    for index, pack in enumerate(PACKS):
        passport = passports.from_document(passport_document(*pack))
        valuation = engine.value(passport)
        record = store.save(valuation_to_dict(valuation), passport=passport)

        seller, region = SELLERS[index % len(SELLERS)]
        try:
            listing = service.create_listing(
                record.reference, seller_handle=seller, region=region
            )
        except MarketError as exc:
            print(f"  {record.reference}  skipped: {exc}")
            continue

        listings.append(listing)
        print(
            f"  {record.reference} -> {listing.reference}  "
            f"{listing.battery_label[:30]:<32s}"
            f"{listing.asking_price:>8,.0f} {listing.currency}  "
            f"{listing.price_verdict.label}"
        )

    print("\noffers\n" + "-" * 62)
    for index, listing in enumerate(listings):
        if listing.kind.value == "disposal":
            continue
        buyer, factor = BUYERS[index % len(BUYERS)]
        offer = service.make_offer(
            listing.reference,
            buyer_handle=buyer,
            amount=round(listing.asking_price * factor),
            message="Can collect within the week.",
        )
        print(
            f"  {buyer:<22s} offered {offer.amount:>8,.0f} on "
            f"{listing.battery_label[:28]}"
        )

    # Close a few, so the market has a price history rather than only asks.
    # Three sales of one model is the minimum before a published figure --
    # below that a single transaction is an anecdote.
    print("\nsales\n" + "-" * 62)
    for listing in listings[:5]:
        if listing.kind.value == "disposal":
            continue
        live = service.get(listing.reference)
        best = live.best_offer
        if best is None:
            continue
        service.accept_offer(best.reference)
        sold = service.mark_sold(live.reference)
        print(
            f"  {sold.battery_label[:32]:<34s}{sold.sold_price:>8,.0f} "
            f"{sold.currency}  ({sold.sold_price / sold.rated_kwh:.1f}/kWh)"
        )

    summary = summarise(service.market.sold())
    print("\nobserved prices (what feeds back into battery-data)\n" + "-" * 62)
    if summary:
        print(json.dumps(summary, indent=2))
    else:
        print("  no model has three completed sales yet")

    print(
        f"\nvaluations: {store.path}\nmarket:     {service.market.path}\n"
        "\nbv serve   then open http://127.0.0.1:8000/market"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
