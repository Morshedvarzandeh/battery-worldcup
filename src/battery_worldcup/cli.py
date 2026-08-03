"""Command-line interface: ``bwc``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .errors import BatteryWorldCupError
from .market.resolver import build_resolver
from .packs.catalogue import load_catalogue
from .passport.models import BatteryPassport
from .passport.resolver import PassportResolver
from .serialisation import passport_to_dict, valuation_to_dict
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
    raise BatteryWorldCupError(
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
    valuation = engine.value(passport)

    if args.json:
        print(json.dumps(valuation_to_dict(valuation), indent=2))
    else:
        print(_render_valuation(valuation))
    return 0


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


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the HTTP API and scan UI."""
    try:
        import uvicorn
    except ImportError:
        print(
            "the API extra is not installed; run: pip install 'battery-worldcup[api]'",
            file=sys.stderr,
        )
        return 2

    uvicorn.run(
        "battery_worldcup.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="bwc",
        description=(
            "Scan a battery passport and find out what the pack is actually worth."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")

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
    except BatteryWorldCupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
