#!/usr/bin/env python3
"""Turn an operator-supplied public price file into a reference dataset.

The public agencies publish prices that may be redistributed; the subscription
services publish better ones that may not. This tool ingests the former and
writes the dataset that
:class:`battery_value.market.providers.reference.ReferenceProvider` reads.

It deliberately does not download anything. The publishers change file names,
sheet layouts and hosting arrangements without notice, and a fetcher that
silently half-works produces a valuation that looks sourced and is not. The
operator downloads the file and points this at it, which also means the run is
reproducible from an artefact that can be archived alongside the output.

Usage::

    # A layout this tool knows
    python tools/import_reference_prices.py \\
        --source worldbank-pinksheet \\
        --input CMO-Historical-Data-Monthly.csv \\
        --period 2026-07 \\
        --output reference_prices.json

    # Anything else, mapped by the operator
    python tools/import_reference_prices.py \\
        --source csv \\
        --input my_extract.csv \\
        --source-key my-agency \\
        --source-title "National Statistics Office price series" \\
        --source-licence CC-BY-4.0 \\
        --output reference_prices.json

**On the built-in layouts.** The column names the ``worldbank-pinksheet`` and
``usgs-mcs`` adapters expect are asserted, not discovered: they were written
against the published layouts, and this repository's CI has no network access
to check them against a live download. Both adapters therefore refuse a file
whose header is not what they expect, naming the mismatch, rather than guessing
at a column and emitting a plausible wrong number. If an adapter refuses a file
you believe is correct, the layout has changed -- use ``--source csv`` with an
extract you have mapped yourself and open an issue.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from battery_value.market.providers.reference import (  # noqa: E402
    REDISTRIBUTABLE_LICENCES,
    SCHEMA_VERSION,
    ReferencePriceError,
    parse_dataset,
)


@dataclass(frozen=True)
class SourceMeta:
    """Publisher facts, which are constants rather than data."""

    key: str
    title: str
    licence: str
    url: str | None = None


# Facts about the publishers, not figures from them.
WORLD_BANK = SourceMeta(
    key="worldbank-pinksheet",
    title="World Bank Commodity Price Data (Pink Sheet)",
    licence="CC-BY-4.0",
    url="https://www.worldbank.org/en/research/commodity-markets",
)
USGS = SourceMeta(
    key="usgs-mcs",
    title="USGS Mineral Commodity Summaries",
    licence="public-domain",
    url="https://www.usgs.gov/centers/national-minerals-information-center",
)

# Series name in the source file -> traded_form code in this package.
# Only forms the package actually prices are mapped; an unmapped column is
# skipped rather than guessed at.
WORLD_BANK_SERIES: dict[str, str] = {
    "NICKEL": "nickel_metal",
    "COPPER": "copper_metal",
    "ALUMINUM": "aluminium_metal",
    "LEAD": "lead_metal",
}
USGS_SERIES: dict[str, str] = {
    "lithium carbonate": "lithium_carbonate",
    "cobalt": "cobalt_metal",
    "graphite": "graphite_flake",
    "manganese": "manganese_sulphate",
}

# Headers each built-in adapter requires. A file that does not carry these is
# refused; see the module docstring.
WORLD_BANK_REQUIRED = ("period",)
USGS_REQUIRED = ("commodity", "unit_value", "currency", "unit", "year")

NEUTRAL_REQUIRED = (
    "form",
    "price",
    "currency",
    "unit",
    "period_start",
    "period_end",
    "basis",
)


class AdapterRefused(SystemExit):
    """The input is not the layout the adapter was written for."""

    def __init__(self, message: str) -> None:
        super().__init__(f"refused: {message}")


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = [f.strip() for f in (reader.fieldnames or [])]
        rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    if not fields:
        raise AdapterRefused(f"{path} has no header row")
    return fields, rows


def _check_headers(path: Path, fields: list[str], required: tuple[str, ...]) -> None:
    lowered = {f.lower() for f in fields}
    missing = [r for r in required if r.lower() not in lowered]
    if missing:
        raise AdapterRefused(
            f"{path}: expected column(s) {', '.join(missing)} not found. "
            f"Header was: {', '.join(fields[:12])}"
            f"{' ...' if len(fields) > 12 else ''}. "
            f"If the published layout has changed, re-run with --source csv "
            f"against an extract you have mapped yourself."
        )


def month_bounds(period: str) -> tuple[date, date]:
    """``'2026-07'`` -> the first and last day of that month."""
    try:
        year_s, month_s = period.split("-", 1)
        year, month = int(year_s), int(month_s)
        start = date(year, month, 1)
    except (ValueError, TypeError) as exc:
        raise AdapterRefused(f"--period {period!r} is not YYYY-MM") from exc
    return start, date(year, month, monthrange(year, month)[1])


def adapt_worldbank(path: Path, period: str) -> tuple[SourceMeta, list[dict[str, Any]]]:
    """Pink Sheet monthly extract -> price entries for one month."""
    fields, rows = _read_csv(path)
    _check_headers(path, fields, WORLD_BANK_REQUIRED)
    start, end = month_bounds(period)

    by_period = {row.get("period", "").strip(): row for row in rows}
    # The Pink Sheet writes months as either 2026M07 or 2026-07.
    row = by_period.get(f"{start.year}M{start.month:02d}") or by_period.get(period)
    if row is None:
        raise AdapterRefused(
            f"{path}: no row for period {period}. "
            f"Available periods include: {', '.join(list(by_period)[-6:])}"
        )

    entries: list[dict[str, Any]] = []
    for series, form in WORLD_BANK_SERIES.items():
        raw = next(
            (v for k, v in row.items() if k.upper().startswith(series) and v), None
        )
        if not raw:
            continue
        try:
            price = float(raw.replace(",", ""))
        except ValueError:
            continue
        entries.append(
            {
                "form": form,
                "price": price,
                "currency": "USD",
                "unit": "t",
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "basis": "monthly_average",
                "source": WORLD_BANK.key,
                "series": series,
            }
        )
    if not entries:
        raise AdapterRefused(
            f"{path}: matched period {period} but no known series in "
            f"{', '.join(WORLD_BANK_SERIES)} carried a value"
        )
    return WORLD_BANK, entries


def adapt_usgs(path: Path, year: int | None) -> tuple[SourceMeta, list[dict[str, Any]]]:
    """USGS annual unit-value extract -> price entries for one year."""
    fields, rows = _read_csv(path)
    _check_headers(path, fields, USGS_REQUIRED)

    years = {int(r["year"]) for r in rows if r.get("year", "").isdigit()}
    if not years:
        raise AdapterRefused(f"{path}: no usable year column values")
    target = year or max(years)
    if target not in years:
        raise AdapterRefused(
            f"{path}: no rows for year {target}; have {', '.join(map(str, sorted(years)))}"
        )

    entries: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("year", "").isdigit() or int(row["year"]) != target:
            continue
        form = USGS_SERIES.get(row.get("commodity", "").lower())
        if form is None:
            continue
        try:
            price = float(row["unit_value"].replace(",", ""))
        except (ValueError, KeyError):
            continue
        entries.append(
            {
                "form": form,
                "price": price,
                "currency": row.get("currency") or "USD",
                "unit": row.get("unit") or "t",
                "period_start": date(target, 1, 1).isoformat(),
                "period_end": date(target, 12, 31).isoformat(),
                # An annual mean is a much weaker claim than a monthly one, and
                # the basis is what tells a reader that.
                "basis": "annual_average",
                "source": USGS.key,
                "series": row.get("commodity"),
            }
        )
    if not entries:
        raise AdapterRefused(
            f"{path}: year {target} carried no commodity in "
            f"{', '.join(sorted(USGS_SERIES))}"
        )
    return USGS, entries


def adapt_neutral(path: Path, meta: SourceMeta) -> tuple[SourceMeta, list[dict[str, Any]]]:
    """The documented neutral CSV, mapped by the operator."""
    fields, rows = _read_csv(path)
    _check_headers(path, fields, NEUTRAL_REQUIRED)
    entries: list[dict[str, Any]] = []
    for line, row in enumerate(rows, start=2):
        if not row.get("form"):
            continue
        try:
            price = float(row["price"].replace(",", ""))
        except ValueError as exc:
            raise AdapterRefused(f"{path}:{line}: price {row['price']!r} is not a number") from exc
        entries.append(
            {
                "form": row["form"],
                "price": price,
                "currency": row["currency"],
                "unit": row["unit"],
                "period_start": row["period_start"],
                "period_end": row["period_end"],
                "basis": row["basis"],
                "source": meta.key,
                "series": row.get("series") or None,
                "region": row.get("region") or None,
            }
        )
    if not entries:
        raise AdapterRefused(f"{path}: no rows carried a form")
    return meta, entries


def build_dataset(
    meta: SourceMeta, entries: list[dict[str, Any]], generated_at: date
) -> dict[str, Any]:
    """Assemble the dataset document, newest entry winning per form."""
    prices: dict[str, dict[str, Any]] = {}
    for entry in entries:
        form = entry.pop("form")
        existing = prices.get(form)
        if existing and existing["period_end"] >= entry["period_end"]:
            continue
        prices[form] = {k: v for k, v in entry.items() if v is not None}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "sources": {
            meta.key: {
                "title": meta.title,
                "licence": meta.licence,
                **({"url": meta.url} if meta.url else {}),
                "retrieved": generated_at.isoformat(),
            }
        },
        "prices": dict(sorted(prices.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--source", required=True, choices=["worldbank-pinksheet", "usgs-mcs", "csv"]
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--period", help="YYYY-MM, for monthly sources")
    parser.add_argument("--year", type=int, help="calendar year, for annual sources")
    parser.add_argument("--generated-at", help="ISO date to stamp; defaults to today")
    parser.add_argument("--source-key")
    parser.add_argument("--source-title")
    parser.add_argument("--source-licence")
    parser.add_argument("--source-url")
    args = parser.parse_args(argv)

    if not args.input.is_file():
        raise SystemExit(f"input not found: {args.input}")
    generated_at = (
        date.fromisoformat(args.generated_at) if args.generated_at else date.today()
    )

    if args.source == "worldbank-pinksheet":
        if not args.period:
            raise SystemExit("--period YYYY-MM is required for worldbank-pinksheet")
        meta, entries = adapt_worldbank(args.input, args.period)
    elif args.source == "usgs-mcs":
        meta, entries = adapt_usgs(args.input, args.year)
    else:
        missing = [
            flag
            for flag, value in (
                ("--source-key", args.source_key),
                ("--source-title", args.source_title),
                ("--source-licence", args.source_licence),
            )
            if not value
        ]
        if missing:
            raise SystemExit(f"--source csv requires {', '.join(missing)}")
        meta, entries = adapt_neutral(
            args.input,
            SourceMeta(
                key=args.source_key,
                title=args.source_title,
                licence=args.source_licence,
                url=args.source_url,
            ),
        )

    if meta.licence not in REDISTRIBUTABLE_LICENCES:
        raise SystemExit(
            f"licence {meta.licence!r} is not redistributable; permitted: "
            f"{', '.join(sorted(REDISTRIBUTABLE_LICENCES))}"
        )

    document = build_dataset(meta, entries, generated_at)

    # Parse what was just built. Writing a file the reader would reject is the
    # one failure this tool must not be capable of.
    try:
        parse_dataset(document)
    except ReferencePriceError as exc:
        raise SystemExit(f"built an unusable dataset: {exc}") from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(document['prices'])} price(s) to {args.output}")
    for form, entry in document["prices"].items():
        print(f"  {form:22} {entry['price']:>12,.2f} {entry['currency']}/{entry['unit']}"
              f"  {entry['period_start']}..{entry['period_end']}  {entry['basis']}")
    print(f"attribution: {meta.title} ({meta.licence})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
