"""Command-line interface: the `bv` command (also installed as `battery-value`)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__, licence
from .errors import BatteryValueError
from .market.resolver import build_resolver
from .packs.catalogue import load_catalogue
from .passport.models import BatteryPassport
from .passport.resolver import PassportResolver
from .report import build_html_report, report_filename
from .serialisation import passport_to_dict, valuation_to_dict
from .store import ValuationStore, default_store, normalise_reference
from .materials.degradation import CLIMATES, DEFAULT_CLIMATE
from .valuation import plain
from .valuation.config import ValuationConfig
from .valuation.engine import ValuationEngine
from .valuation.models import ResidualValuation

_RULE = "-" * 74


def _build_engine(args: argparse.Namespace) -> ValuationEngine:
    config = ValuationConfig(currency=args.currency.upper())
    return ValuationEngine(
        config=config,
        prices=build_resolver(
            currency=config.currency,
            offline=args.offline,
            csv_path=getattr(args, "price_csv", None),
        ),
    )


def _load_passport(args: argparse.Namespace) -> BatteryPassport:
    resolver = PassportResolver(allow_private_hosts=args.allow_private_hosts)
    if args.file:
        return resolver.from_file(args.file)
    if args.image:
        return resolver.from_image(args.image)
    if args.qr:
        return resolver.from_qr(args.qr)
    if not sys.stdin.isatty():
        document = json.load(sys.stdin)
        return resolver.from_document(document)
    raise BatteryValueError(
        "no passport supplied: pass --file, --qr or --image, or pipe JSON on stdin"
    )


def _render_valuation(valuation: ResidualValuation) -> str:
    """Human-readable valuation report."""
    out: list[str] = []
    add = out.append

    add(_RULE)
    add(f"  {valuation.battery_label}")
    add(_RULE)
    add(
        f"  {valuation.rated_kwh:g} kWh nameplate | "
        f"{valuation.state_of_health:.0%} state of health | "
        f"{valuation.bom.chemistry.key} | {valuation.bom.pack_mass_kg:.0f} kg"
    )
    add("")
    add(f"  RESIDUAL VALUE   {valuation.residual_value.format(0)}")
    add(
        f"  per kWh          {valuation.value_per_kwh.format(2)}"
        f"    confidence {valuation.confidence:.0%}"
    )
    if valuation.value_range:
        add(f"  range            {valuation.value_range.describe()}")
        add(f"  main driver      {valuation.value_range.driver}")
    add("")

    aging = valuation.aging
    if aging is not None:
        add(f"  WEAR ({aging.verdict.label.lower()})")
        add(f"    {plain.aging_headline(aging)}")
        add(f"    {plain.aging_outlook(aging)}")
        if aging.is_comparable:
            add(
                f"    measured {aging.observed_soh:.1%} vs "
                f"{aging.expected_soh:.1%} typical "
                f"(+/-{aging.spread_points:.1f} points across the model), "
                f"fading at {aging.fade_ratio:.2f}x the usual rate"
            )
        if aging.cycles_used and aging.cycles_expected:
            add(
                f"    {aging.cycles_used:,} cycles against "
                f"{aging.cycles_expected:,.0f} typical for its age"
            )
        add(
            f"    curve: {aging.profile_label}"
            + ("" if aging.is_model_specific else " (chemistry fallback)")
            + f", {aging.climate} climate"
        )
        add("")

    add("  PATHWAYS")
    for pathway in valuation.pathways:
        marker = "*" if pathway is valuation.recommended else " "
        status = pathway.net_value.format(0) if pathway.eligible else "not available"
        add(f"   {marker} {pathway.label:<38s} {status:>14s}  conf {pathway.confidence:>4.0%}")
        if not pathway.eligible:
            for blocker in pathway.blockers:
                add(f"        - {blocker}")
    add("")

    best = valuation.recommended
    if best is not None:
        add(f"  BREAKDOWN: {best.label}")
        for line in best.revenue_lines():
            add(f"    + {line.label:<44s} {line.amount.format(0):>12s}")
            if line.detail:
                add(f"        {line.detail}")
        for line in best.cost_lines():
            add(f"    - {line.label:<44s} {line.amount.format(0):>12s}")
            if line.detail:
                add(f"        {line.detail}")
        add(f"    {'=' * 60}")
        add(f"      {'NET':<44s} {best.net_value.format(0):>12s}")
        add("")
        if best.assumptions:
            add("  ASSUMPTIONS")
            for assumption in best.assumptions:
                add(f"    - {assumption}")
            add("")

    add("  BILL OF MATERIALS")
    for line in valuation.bom.sorted_lines():
        add(
            f"    {line.element:<3s} {line.mass_kg:9.2f} kg   {line.source:<9s} {line.basis}"
        )
    add(f"    {'inert':<3s} {valuation.bom.inert_mass_kg:9.2f} kg")
    add("")

    add(
        f"  PRICES ({valuation.prices.currency}, confidence "
        f"{valuation.prices.confidence:.0%}, sources: "
        f"{', '.join(f'{k} x{v}' for k, v in valuation.prices.sources_used().items())})"
    )
    for quote in valuation.prices.quotes.values():
        add(f"    {quote.describe()}")
    add("")

    if valuation.sensitivity:
        add("  SENSITIVITY")
        for factor in valuation.sensitivity:
            add(f"    {factor.describe()}")
        add("")

    if valuation.warnings:
        add("  WARNINGS")
        for warning in valuation.warnings:
            add(f"    ! {warning}")
        add("")

    add(_RULE)
    return "\n".join(out)


def cmd_value(args: argparse.Namespace) -> int:
    """Value a battery pack."""
    passport = _load_passport(args)
    engine = _build_engine(args)
    valuation = engine.value(
        passport, climate=getattr(args, "climate", DEFAULT_CLIMATE)
    )

    payload = valuation_to_dict(valuation)
    store = ValuationStore(enabled=not args.no_store)
    record = store.save(payload, passport=passport)

    if args.report is not None:
        destination = Path(args.report)
        if destination.is_dir():
            destination = destination / report_filename(payload)
        destination.write_text(
            build_html_report(payload, include_technical=not args.summary_only),
            encoding="utf-8",
        )
        print(f"report written to {destination}", file=sys.stderr)

    if args.json:
        print(json.dumps(payload, indent=2))
    elif not args.quiet:
        print(_render_valuation(valuation))
        if record is not None:
            print(f"  Reference: {record.reference}")
            print("  Quote it to get this exact valuation back, prices and all.")
            print()
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """List valuations already on record."""
    store = default_store()
    records = (
        store.find_by_battery(args.battery, limit=args.limit)
        if args.battery
        else store.recent(limit=args.limit)
    )

    if args.json:
        print(json.dumps(
            [
                {
                    "reference": record.reference,
                    "created_at": record.created_at.isoformat(),
                    "battery_label": record.battery_label,
                    "serial_number": record.serial_number,
                    "residual_value": record.residual_value,
                    "currency": record.currency,
                    "confidence": record.confidence,
                }
                for record in records
            ],
            indent=2,
        ))
        return 0

    if not records:
        print("no valuations on record")
        return 0

    print(f"{'reference':<14s} {'date':<11s} {'battery':<38s} {'value':>14s}")
    print("-" * 80)
    for record in records:
        print(record.summary_line())
    print(f"\n{len(records)} of {store.count()} record(s) at {store.path}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Reprint a stored valuation, exactly as it was produced."""
    record = default_store().get(args.reference)
    if record is None:
        print(
            f"error: no valuation found for {normalise_reference(args.reference)}",
            file=sys.stderr,
        )
        return 1

    if args.report is not None:
        destination = Path(args.report)
        if destination.is_dir():
            destination = destination / report_filename(record.payload)
        destination.write_text(
            build_html_report(record.payload, include_technical=not args.summary_only),
            encoding="utf-8",
        )
        print(f"report written to {destination}", file=sys.stderr)

    if args.json:
        print(json.dumps(record.payload, indent=2))
    elif not args.quiet:
        print(_render_stored(record))
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    """Erase a stored valuation."""
    if default_store().delete(args.reference):
        print(f"deleted {normalise_reference(args.reference)}")
        return 0
    print(
        f"error: no valuation found for {normalise_reference(args.reference)}",
        file=sys.stderr,
    )
    return 1


def cmd_prune(args: argparse.Namespace) -> int:
    """Delete records past the retention period."""
    store = default_store()
    days = args.days if args.days is not None else store.retention_days
    if days <= 0:
        # A zero retention would silently wipe the store, which is never what
        # someone reaching for `prune` meant.
        print(
            "error: retention must be at least 1 day. To clear everything, "
            f"delete {store.path}",
            file=sys.stderr,
        )
        return 1

    removed = store.prune(days)
    print(f"removed {removed} record(s) older than {days} days")
    return 0


def cmd_market(args: argparse.Namespace) -> int:
    """Browse, sell into, and settle the market."""
    from .marketplace import MarketError, MarketService
    from .marketplace.observations import summarise, to_battery_data_sql

    service = MarketService()

    try:
        if args.market_command == "browse":
            listings = service.search(
                query=args.search, chemistry=args.chemistry, limit=args.limit
            )
            if args.json:
                print(json.dumps([listing.to_dict() for listing in listings], indent=2))
                return 0
            if not listings:
                print("nothing listed")
                return 0
            print(f"{'reference':<14s}{'battery':<34s}{'asking':>10s}  price")
            print("-" * 78)
            for listing in listings:
                asking = (
                    "collection"
                    if listing.kind.value == "disposal"
                    else f"{listing.asking_price:,.0f}"
                )
                print(
                    f"{listing.reference:<14s}{listing.battery_label[:32]:<34s}"
                    f"{asking:>10s}  {listing.price_verdict.label}"
                )
            print(f"\n{len(listings)} listing(s)")
            return 0

        if args.market_command == "show":
            listing = service.get(args.reference)
            if listing is None:
                print(f"error: no listing {args.reference}", file=sys.stderr)
                return 1
            if args.json:
                print(json.dumps(listing.to_dict(), indent=2))
                return 0
            print(_render_listing(listing))
            return 0

        if args.market_command == "sell":
            listing = service.create_listing(
                args.valuation,
                seller_handle=args.seller,
                asking_price=args.price,
                region=args.region or "",
                title=args.title or "",
                description=args.description or "",
            )
            print(_render_listing(listing))
            return 0

        if args.market_command == "offer":
            offer = service.make_offer(
                args.reference,
                buyer_handle=args.buyer,
                amount=args.amount,
                message=args.message or "",
            )
            print(
                f"{offer.reference}: offered {offer.amount:,.0f} {offer.currency} "
                f"on {offer.listing_reference}"
            )
            return 0

        if args.market_command == "accept":
            listing = service.accept_offer(args.reference)
            print(f"{listing.reference} is now {listing.status.label.lower()}")
            return 0

        if args.market_command == "sold":
            listing = service.mark_sold(args.reference, args.price)
            print(
                f"{listing.reference} sold for {listing.sold_price:,.0f} "
                f"{listing.currency}"
            )
            return 0

        if args.market_command == "prices":
            sold = service.market.sold()
            if args.sql:
                print(to_battery_data_sql(sold))
                return 0
            summary = summarise(sold)
            if args.json:
                print(json.dumps(summary, indent=2))
                return 0
            if not summary:
                print(
                    f"no model has three completed sales yet ({len(sold)} sale(s) "
                    "recorded). One transaction is an anecdote, not a price."
                )
                return 0
            print(f"{'pack':<34s}{'median/kWh':>12s}{'sales':>7s}  health")
            print("-" * 70)
            for entry in summary.values():
                print(
                    f"{entry['label'][:32]:<34s}"
                    f"{entry['median_price_per_kwh']:>12,.1f}"
                    f"{entry['sample_size']:>7d}  "
                    f"{entry['min_soh']:.0%}-{entry['max_soh']:.0%}"
                )
            return 0
    except MarketError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("error: unknown market command", file=sys.stderr)
    return 1


def cmd_certify(args: argparse.Namespace) -> int:
    """Issue a signed certificate for a stored valuation."""
    from .passport.resolver import PassportResolver
    from .trust import certificate as certificate_module
    from .trust.signing import SigningUnavailable

    record = default_store().get(args.reference)
    if record is None:
        print(
            f"error: no valuation found for {normalise_reference(args.reference)}",
            file=sys.stderr,
        )
        return 1

    document = record.payload.get("passport")
    if document is None:
        print(
            "error: this record predates certificates and has no passport stored. "
            "Re-scan the battery to issue one.",
            file=sys.stderr,
        )
        return 1

    try:
        certificate = certificate_module.issue(
            record, PassportResolver().from_document(document)
        )
    except SigningUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = certificate.to_dict()
    if args.output:
        Path(args.output).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        print(f"wrote {args.output}")
    if args.json:
        print(json.dumps(payload, indent=2))
    elif not args.output:
        print(_render_certificate(certificate))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Check a certificate file."""
    from .trust import certificate as certificate_module

    try:
        document = json.loads(Path(args.file).read_text(encoding="utf-8"))
        certificate = certificate_module.from_dict(document)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: not a certificate: {exc}", file=sys.stderr)
        return 1

    if certificate.verify():
        issuer = certificate.signature.issuer if certificate.signature else "unknown"
        print(f"{certificate.reference}: intact, issued by {issuer}")
        print(f"  {certificate.strength_in_words()}")
        return 0

    print(
        f"{certificate.reference}: DOES NOT VERIFY. The record has been altered "
        "since it was issued, or it was never signed by the key it names.",
        file=sys.stderr,
    )
    return 2


def cmd_portfolio(args: argparse.Namespace) -> int:
    """What everything on record is worth, and what waiting costs."""
    from . import portfolio as portfolio_module

    records = default_store().recent(limit=args.limit)
    book = portfolio_module.build(records, currency=args.currency.upper())

    if args.json:
        print(json.dumps(portfolio_module.to_dict(book), indent=2))
        return 0

    if not book.holdings:
        print("nothing on record")
        return 0

    out: list[str] = [_RULE, "  PORTFOLIO", _RULE]
    out.append(f"  batteries        {len(book.holdings):,}")
    out.append(f"  energy           {book.energy_kwh:,.0f} kWh")
    out.append(
        f"  value            {book.value.format(0)}  "
        f"({book.value_per_kwh:,.0f} {book.currency}/kWh)"
    )
    out.append(
        f"  losing           {book.monthly_loss.format(0)} a month  "
        f"({book.loss_rate:.1%} a year)"
    )
    if book.urgent:
        out.append(
            f"  at the cliff     {len(book.urgent)} pack(s) holding "
            f"{book.value_at_risk.format(0)} drop below resale grade within "
            f"{portfolio_module.URGENT_HORIZON_YEARS:.0f} years"
        )
    if book.liabilities:
        out.append(f"  liabilities      {len(book.liabilities)} cost money to dispose of")
    out.append(f"  concentration    {book.concentration(0.8)} pack(s) hold 80% of the value")
    out.append("")

    groups = book.by("pack_model_key")[:10]
    if groups:
        out.append(f"  BY MODEL")
        out.append(f"    {'model':<34s}{'n':>4s}{'value':>12s}{'/kWh':>9s}{'loss/yr':>11s}")
        for group in groups:
            out.append(
                f"    {group.label[:32]:<34s}{group.count:>4d}"
                f"{group.value:>12,.0f}{group.value_per_kwh:>9,.0f}"
                f"{group.annual_loss:>11,.0f}"
            )
        out.append("")

    if book.urgent:
        out.append("  MOVE THESE FIRST")
        for holding in book.urgent[:10]:
            out.append(
                f"    {holding.reference}  {holding.label[:28]:<30s}"
                f"{holding.value:>9,.0f}  "
                f"{holding.years_to_resale_floor:.1f} yr to the floor"
            )
        out.append("")

    out.append(_RULE)
    print("\n".join(out))
    return 0


def cmd_forecast(args: argparse.Namespace) -> int:
    """What a pack will be worth later, and what the warranty is worth now."""
    from .passport.resolver import PassportResolver
    from .valuation import forecast as forecast_module

    record = default_store().get(args.reference)
    if record is None:
        print(
            f"error: no valuation found for {normalise_reference(args.reference)}",
            file=sys.stderr,
        )
        return 1
    document = record.payload.get("passport")
    if document is None:
        print(
            "error: this record has no passport stored. Re-scan the battery.",
            file=sys.stderr,
        )
        return 1

    engine = _build_engine(args)
    forecast = forecast_module.build(
        PassportResolver().from_document(document),
        engine,
        years=args.years,
        climate=args.climate,
    )

    if args.json:
        print(json.dumps(forecast_module.to_dict(forecast), indent=2))
        return 0

    out = [_RULE, f"  FORECAST {record.battery_label}", _RULE]
    out.append(f"  {forecast.summary()}")
    out.append("")
    out.append(
        f"    {'date':<12}{'health':>8}{'value':>11}{'low':>10}{'high':>10}   warranty"
    )
    for point in forecast.points:
        out.append(
            f"    {point.on.isoformat():<12}{point.state_of_health:>7.1%}"
            f"{point.value.amount:>11,.0f}{point.low.amount:>10,.0f}"
            f"{point.high.amount:>10,.0f}   "
            f"{'covered' if point.under_warranty else 'exposed'}"
        )
    out.append("")
    if forecast.warranty_value is not None:
        out.append(
            f"  warranty left    {forecast.warranty_value.format(0)}  "
            f"({forecast.warranty_claim_probability:.0%} chance of a claim before "
            "it expires)"
        )
    out.append(
        f"  cost of doubt    {forecast.uncertainty_discount().format(0)} at the "
        f"horizon, which is what evidence is worth on this pack"
    )
    out.append(_RULE)
    print("\n".join(out))
    return 0


def _render_certificate(certificate) -> str:
    """A certificate as a person reads it: who said what."""
    out: list[str] = [_RULE]
    out.append(f"  CERTIFICATE {certificate.reference}")
    out.append(_RULE)
    out.append(f"  {certificate.subject.get('label', 'Battery')}")
    out.append(f"  issued {certificate.issued_at:%Y-%m-%d} by "
               f"{certificate.signature.issuer if certificate.signature else 'nobody'}")
    out.append(f"  signature {'verifies' if certificate.verify() else 'DOES NOT VERIFY'}")
    out.append("")
    out.append(f"  {certificate.strength_in_words()}")
    out.append("")
    out.append("  WHO SAID WHAT")
    for claim in certificate.claims:
        value = "-" if claim.value in (None, "") else str(claim.value)
        out.append(
            f"    {claim.label[:38]:<40s}{value[:22]:<24s}{claim.basis.label}"
        )
    out.append("")

    compliance = certificate.compliance
    out.append(f"  EU 2023/1542: {compliance.get('summary', '')}")
    for requirement in compliance.get("requirements", []):
        if requirement["is_a_gap"]:
            out.append(
                f"    missing: {requirement['label']} "
                f"({requirement['article']}, {requirement['owner']}'s to supply)"
            )
    out.append("")
    out.append(f"  {certificate.attestation}")
    out.append(_RULE)
    return "\n".join(out)


def _render_listing(listing) -> str:
    """One listing, with the price shown against its own valuation."""
    out: list[str] = []
    add = out.append
    guide = listing.guide

    add(_RULE)
    add(f"  {listing.display_title()}")
    add(_RULE)
    add(
        f"  {listing.reference} | {listing.status.label} | "
        f"from valuation {listing.valuation_reference}"
    )
    add(
        f"  {listing.rated_kwh:g} kWh | {listing.state_of_health:.0%} health "
        f"({listing.health_source}) | {listing.chemistry}"
        + (f" | {listing.region}" if listing.region else "")
    )
    add("")

    if listing.kind.value == "disposal":
        add("  DISPOSAL, NOT A SALE")
    else:
        add(f"  ASKING           {listing.asking_price:,.0f} {listing.currency}")
        add(
            f"  guide            {guide.low:,.0f} - {guide.high:,.0f} "
            f"(mid {guide.guide:,.0f})"
        )
        add(f"  valuation        {listing.estimate:,.0f} {listing.currency} end to end")
        add(f"  verdict          {listing.price_verdict.label}")
    add(f"    {listing.price_note}")
    add("")

    if listing.wear_headline:
        add("  WEAR")
        add(f"    {listing.wear_headline}")
        add("")

    if listing.needs_dangerous_goods_freight:
        add("  ! Recorded as damaged: ADR special provision 376 applies, which")
        add("    means a different carrier and a materially higher freight cost.")
        add("")

    if listing.offers:
        add("  OFFERS")
        for offer in listing.offers:
            add(
                f"    {offer.reference}  {offer.amount:>9,.0f} {offer.currency}  "
                f"{offer.status.value:<9s} {offer.buyer_handle}"
            )
        add("")

    add(f"  Contact: {listing.seller_handle}")
    add(_RULE)
    return "\n".join(out)


def _render_stored(record) -> str:
    """Render a stored valuation from its payload."""
    payload = record.payload
    plain = payload.get("plain", {})
    battery = payload.get("battery", {})
    out: list[str] = [
        _RULE,
        f"  {record.battery_label}",
        _RULE,
        f"  Reference {record.reference}   valued {record.created_at:%d %B %Y}"
        f" ({record.age_days} days ago)",
        "",
        f"  {plain.get('headline', '')}",
        "",
        f"  {plain.get('confidence', {}).get('label', '')}."
        f" {plain.get('confidence', {}).get('explanation', '')}",
        "",
        "  OPTIONS",
    ]
    for option in sorted(
        payload.get("pathways", []),
        key=lambda p: (not p.get("eligible"), -float(p["net_value"]["amount"])),
    ):
        marker = "*" if option.get("pathway") == payload.get("recommended_pathway") else " "
        amount = (
            option["net_value"]["formatted"]
            if option.get("eligible")
            else "not possible"
        )
        out.append(f"   {marker} {option.get('friendly_label', ''):<38s} {amount:>14s}")
        if not option.get("eligible") and option.get("blockers"):
            out.append(f"        - {option['blockers'][0]}")
    out.extend([
        "",
        f"  Prices as at {payload.get('prices', {}).get('oldest_as_of', 'n/a')}"
        " -- this is the valuation as originally produced, not a re-run.",
        "",
        f"  {battery.get('rated_kwh', '')} kWh | "
        f"{float(battery.get('state_of_health', 0)) * 100:.0f}% health | "
        f"{plain.get('chemistry', '')}",
        _RULE,
    ])
    return "\n".join(out)


def cmd_scan(args: argparse.Namespace) -> int:
    """Read a passport without valuing it."""
    passport = _load_passport(args)
    print(json.dumps(passport_to_dict(passport), indent=2, default=str))
    return 0


def cmd_prices(args: argparse.Namespace) -> int:
    """Show resolved market prices and where they came from."""
    resolver = build_resolver(
        currency=args.currency.upper(),
        offline=args.offline,
        csv_path=getattr(args, "price_csv", None),
    )
    from .compounds import TRADED_FORMS

    price_set = resolver.resolve_many(sorted(TRADED_FORMS))

    if args.json:
        print(
            json.dumps(
                {
                    "currency": price_set.currency,
                    "confidence": price_set.confidence,
                    "quotes": [
                        {
                            "form": form,
                            "price": quote.price,
                            "unit": quote.unit.value,
                            "currency": quote.currency,
                            "per_kg_contained": quote.price_per_kg_contained(),
                            "as_of": quote.as_of.isoformat(),
                            "source": quote.source,
                            "quality": quote.quality.value,
                        }
                        for form, quote in price_set.quotes.items()
                    ],
                    "missing": list(price_set.missing),
                },
                indent=2,
            )
        )
        return 0

    print("Provider chain:")
    for line in resolver.describe_chain():
        print(f"  {line}")
    print()
    header = f"{'form':<20s} {'price':>12s} {'unit':<5s} {'per kg contained':>18s}  source"
    print(header)
    print("-" * len(header))
    for form, quote in price_set.quotes.items():
        print(
            f"{form:<20s} {quote.price:>12,.2f} {quote.unit.value:<5s} "
            f"{quote.price_per_kg_contained():>18,.2f}  {quote.source} "
            f"[{quote.quality.value}, {quote.as_of.isoformat()}]"
        )
    if price_set.missing:
        print(f"\nunpriced: {', '.join(price_set.missing)}")
    print(f"\noverall price confidence: {price_set.confidence:.0%}")
    return 0


def cmd_packs(args: argparse.Namespace) -> int:
    """List or search the pack model catalogue."""
    catalogue = load_catalogue()
    models = catalogue.models
    if args.search:
        needle = args.search.lower()
        models = tuple(
            model
            for model in models
            if needle in model.label.lower()
            or needle in model.manufacturer.lower()
            or any(needle in vehicle.lower() for vehicle in model.vehicle_models)
        )

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "key": model.key,
                        "label": model.label,
                        "manufacturer": model.manufacturer,
                        "chemistry": model.chemistry,
                        "rated_kwh": model.rated_kwh,
                        "pack_mass_kg": model.pack_mass_kg,
                        "module_count": model.module_count,
                        "vehicle_models": list(model.vehicle_models),
                        "components": [
                            {
                                "key": component.key,
                                "label": component.label,
                                "count": component.count,
                                "total_mass_kg": round(component.total_mass_kg, 1),
                                "reusable": component.reusable,
                                "total_value_eur": component.total_value_eur,
                            }
                            for component in model.components
                        ],
                    }
                    for model in models
                ],
                indent=2,
            )
        )
        return 0

    header = f"{'key':<28s} {'chemistry':<10s} {'kWh':>7s} {'kg':>6s} {'mods':>5s}  label"
    print(header)
    print("-" * len(header))
    for model in models:
        print(
            f"{model.key:<28s} {model.chemistry:<10s} {model.rated_kwh:>7.1f} "
            f"{model.pack_mass_kg:>6.0f} {model.module_count or 0:>5d}  {model.label}"
        )
    print(f"\n{len(models)} model(s)")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Refresh the bundled pack catalogue from battery-data."""
    from .packs.battery_data import battery_data_providers, refresh_snapshot

    providers = battery_data_providers()
    if not providers:
        print(
            "error: no battery-data source configured. Set BV_BATTERY_DATA_DSN "
            "for a Postgres database, or BV_BATTERY_DATA_URL for the HTTP API.",
            file=sys.stderr,
        )
        return 1

    print(f"reading from {providers[0].describe()}", file=sys.stderr)
    count, destination = refresh_snapshot(providers[0], path=args.output)
    print(f"wrote {count} pack model(s) to {destination}", file=sys.stderr)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the HTTP API and scan UI."""
    try:
        import uvicorn
    except ImportError:
        print(
            "the API extra is not installed; run: pip install 'battery-value[api]'",
            file=sys.stderr,
        )
        return 2

    uvicorn.run(
        "battery_value.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="bv",
        description=(
            "Scan a battery passport and find out what the pack is actually worth."
        ),
        epilog=(
            f"battery-value {__version__}. Free software under "
            f"{licence.LICENCE}, with no warranty. Source: "
            f"{licence.source_url()}"
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    # argparse runs the version string through the help formatter, which
    # collapses whitespace, so it has to read well as one line.
    parser.add_argument(
        "--version",
        action="version",
        version=(
            f"battery-value {__version__} — {licence.LICENCE}, free software "
            f"with no warranty. Source: {licence.source_url()}"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_input_arguments(target: argparse.ArgumentParser) -> None:
        source = target.add_mutually_exclusive_group()
        source.add_argument("--file", type=Path, help="passport JSON file")
        source.add_argument("--qr", help="raw QR payload (URL, JSON or identifier)")
        source.add_argument("--image", type=Path, help="image containing a QR code")
        target.add_argument(
            "--allow-private-hosts",
            action="store_true",
            help="permit fetching passports from private/loopback addresses",
        )

    def add_market_arguments(target: argparse.ArgumentParser) -> None:
        target.add_argument("--currency", default="EUR", help="reporting currency")
        target.add_argument(
            "--offline",
            action="store_true",
            help="skip network providers and use the bundled snapshot",
        )
        target.add_argument(
            "--price-csv", type=Path, help="CSV of price assessments to prefer"
        )
        target.add_argument("--json", action="store_true", help="emit JSON")

    value_parser = subparsers.add_parser("value", help="value a battery pack")
    add_input_arguments(value_parser)
    add_market_arguments(value_parser)
    value_parser.add_argument(
        "--report",
        metavar="PATH",
        help=(
            "also write a self-contained HTML report to PATH (or into PATH if it "
            "is a directory). Opens anywhere and prints to PDF, so it can be sent "
            "to a buyer, garage or recycler."
        ),
    )
    value_parser.add_argument(
        "--summary-only",
        action="store_true",
        help="leave the technical detail out of the report",
    )
    value_parser.add_argument(
        "--quiet", action="store_true", help="suppress the terminal report"
    )
    value_parser.add_argument(
        "--climate",
        choices=CLIMATES,
        default=DEFAULT_CLIMATE,
        help=(
            "where the pack has spent its life. Heat is the main thing that "
            "separates two otherwise identical batteries, so set it when it is "
            "genuinely known"
        ),
    )
    value_parser.set_defaults(func=cmd_value)

    scan_parser = subparsers.add_parser("scan", help="read a passport, no valuation")
    add_input_arguments(scan_parser)
    scan_parser.set_defaults(func=cmd_scan)

    prices_parser = subparsers.add_parser("prices", help="show market prices in use")
    add_market_arguments(prices_parser)
    prices_parser.set_defaults(func=cmd_prices)

    packs_parser = subparsers.add_parser("packs", help="browse the pack catalogue")
    packs_parser.add_argument("--search", help="filter by model, maker or vehicle")
    packs_parser.add_argument("--json", action="store_true", help="emit JSON")
    packs_parser.set_defaults(func=cmd_packs)

    value_parser.add_argument(
        "--no-store",
        action="store_true",
        help="do not keep this valuation on record",
    )

    history_parser = subparsers.add_parser(
        "history", help="list valuations already on record"
    )
    history_parser.add_argument(
        "--battery", help="serial number or battery id, to see one pack's history"
    )
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.add_argument("--json", action="store_true", help="emit JSON")
    history_parser.set_defaults(func=cmd_history)

    show_parser = subparsers.add_parser(
        "show", help="reprint a stored valuation by reference"
    )
    show_parser.add_argument("reference", help="e.g. BV-7K2P-M4X9")
    show_parser.add_argument("--json", action="store_true", help="emit JSON")
    show_parser.add_argument(
        "--report", metavar="PATH", help="also write the HTML report to PATH"
    )
    show_parser.add_argument(
        "--summary-only",
        action="store_true",
        help="leave the technical detail out of the report",
    )
    show_parser.add_argument("--quiet", action="store_true")
    show_parser.set_defaults(func=cmd_show)

    forget_parser = subparsers.add_parser(
        "forget", help="erase a stored valuation"
    )
    forget_parser.add_argument("reference")
    forget_parser.set_defaults(func=cmd_forget)

    prune_parser = subparsers.add_parser(
        "prune", help="delete records past the retention period"
    )
    prune_parser.add_argument(
        "--days", type=int, default=None, help="override the retention period"
    )
    prune_parser.set_defaults(func=cmd_prune)

    sync_parser = subparsers.add_parser(
        "sync", help="refresh the bundled pack catalogue from battery-data"
    )
    sync_parser.add_argument(
        "--output",
        metavar="PATH",
        help="write somewhere other than the bundled catalogue",
    )
    sync_parser.set_defaults(func=cmd_sync)

    market_parser = subparsers.add_parser(
        "market", help="the marketplace: browse, sell, offer, settle"
    )
    market_subs = market_parser.add_subparsers(dest="market_command", required=True)

    browse = market_subs.add_parser("browse", help="what is for sale")
    browse.add_argument("--search", help="free text over label, title and notes")
    browse.add_argument("--chemistry", help="filter by cell chemistry")
    browse.add_argument("--limit", type=int, default=50)
    browse.add_argument("--json", action="store_true")

    show_listing = market_subs.add_parser("show", help="one listing in full")
    show_listing.add_argument("reference")
    show_listing.add_argument("--json", action="store_true")

    sell = market_subs.add_parser(
        "sell",
        help="list a pack, from a valuation reference",
        description=(
            "A listing can only be created from a valuation, so the buyer sees "
            "the same independent assessment the seller did."
        ),
    )
    sell.add_argument("valuation", help="valuation reference, e.g. BV-7K2P-M4X9")
    sell.add_argument("--seller", required=True, help="how buyers reach you")
    sell.add_argument(
        "--price", type=float, default=None, help="defaults to the guide price"
    )
    sell.add_argument("--region", help="where the pack is, for collection")
    sell.add_argument("--title")
    sell.add_argument("--description")

    offer = market_subs.add_parser("offer", help="bid on a listing")
    offer.add_argument("reference")
    offer.add_argument("--buyer", required=True)
    offer.add_argument("--amount", type=float, required=True)
    offer.add_argument("--message")

    accept = market_subs.add_parser("accept", help="accept an offer")
    accept.add_argument("reference", help="offer reference, e.g. OF-3QRT-8WBN")

    sold = market_subs.add_parser("sold", help="record a completed sale")
    sold.add_argument("reference", help="listing reference")
    sold.add_argument("--price", type=float, default=None)

    prices = market_subs.add_parser("prices", help="what packs actually sold for")
    prices.add_argument("--json", action="store_true")
    prices.add_argument(
        "--sql",
        action="store_true",
        help="render as battery-data rows, ready for review",
    )

    market_parser.set_defaults(func=cmd_market)

    certify_parser = subparsers.add_parser(
        "certify",
        help="issue a signed certificate for a stored valuation",
        description=(
            "A certificate records who stated each fact about the battery and "
            "makes the record tamper-evident, so a buyer can check it without "
            "trusting the seller."
        ),
    )
    certify_parser.add_argument("reference", help="valuation reference")
    certify_parser.add_argument("--output", metavar="PATH", help="write the JSON here")
    certify_parser.add_argument("--json", action="store_true")
    certify_parser.set_defaults(func=cmd_certify)

    verify_parser = subparsers.add_parser(
        "verify", help="check a certificate file"
    )
    verify_parser.add_argument("file", help="a certificate JSON file")
    verify_parser.set_defaults(func=cmd_verify)

    forecast_parser = subparsers.add_parser(
        "forecast",
        help="what a pack will be worth later, and what its warranty is worth",
        description=(
            "Re-values the pack at each horizon rather than extrapolating, "
            "because most packs cross the resale floor within a few years and a "
            "straight line through that cliff reports a number that cannot "
            "happen."
        ),
    )
    forecast_parser.add_argument("reference", help="valuation reference")
    forecast_parser.add_argument("--years", type=float, default=5.0)
    forecast_parser.add_argument(
        "--climate", choices=CLIMATES, default=DEFAULT_CLIMATE
    )
    add_market_arguments(forecast_parser)
    forecast_parser.set_defaults(func=cmd_forecast)

    portfolio_parser = subparsers.add_parser(
        "portfolio",
        help="what everything on record is worth, and what waiting costs",
    )
    portfolio_parser.add_argument("--currency", default="EUR")
    portfolio_parser.add_argument("--limit", type=int, default=500)
    portfolio_parser.add_argument("--json", action="store_true")
    portfolio_parser.set_defaults(func=cmd_portfolio)

    serve_parser = subparsers.add_parser("serve", help="run the API and scan UI")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--reload", action="store_true")
    serve_parser.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        return int(args.func(args) or 0)
    except BatteryValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
