"""Public-agency reference prices: citable, redistributable, and lagging.

The subscription assessments (Fastmarkets, Benchmark, SMM, Argus) are the right
numbers and cannot be redistributed, which is why neither this package nor
``battery-data`` ships them. That left a gap: with no key and no local export,
the only thing standing between contained metal and a money figure was the
bundled snapshot, which is hand-maintained and therefore always ageing.

This provider fills that gap with series that *can* be redistributed -- the
World Bank Pink Sheet, USGS Mineral Commodity Summaries, and anything else the
operator can point at under a licence that permits it.

Two things keep it honest:

* **A period, not a day.** These series publish an average over a month or a
  year. The window travels with the quote so a monthly mean is never read as a
  spot price struck on the last day of the month.
* **A licence check that refuses.** An entry whose source licence is not on the
  redistributable list raises at load rather than loading with a warning. A
  warning would be discovered after the data had been redistributed, which is
  exactly too late, and the same rule is enforced in the schema on the
  ``battery-data`` side.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..types import PriceQuality, PriceQuote
from .base import PriceProvider

logger = logging.getLogger(__name__)

_ENV_DATASET_PATH = "BV_REFERENCE_PRICES"
SCHEMA_VERSION = 1

# Licences under which a price may be stored and passed on. Deliberately short:
# every entry here has been checked to permit redistribution with attribution,
# which the provenance fields carry. Anything absent is refused by name rather
# than silently dropped, so a new source fails loudly and gets reviewed.
REDISTRIBUTABLE_LICENCES: frozenset[str] = frozenset(
    {
        "public-domain",  # US Government works, e.g. USGS
        "CC0-1.0",
        "CC-BY-4.0",  # World Bank open data
        "CC-BY-SA-4.0",
        "ODC-BY-1.0",
    }
)

# How a published number was arrived at. Kept explicit because an annual mean
# and a month-end spot are not interchangeable inputs to a valuation.
PRICE_BASES: frozenset[str] = frozenset(
    {"monthly_average", "quarterly_average", "annual_average", "spot", "unit_value"}
)


class ReferencePriceError(ValueError):
    """A reference dataset that cannot be trusted as loaded."""


@dataclass(frozen=True, slots=True)
class ReferenceSource:
    """Where one series came from, and on what terms."""

    key: str
    title: str
    licence: str
    url: str | None = None
    retrieved: date | None = None

    def attribution(self) -> str:
        """Attribution line, which is what CC-BY actually requires."""
        bits = [self.title]
        if self.url:
            bits.append(self.url)
        bits.append(self.licence)
        return " - ".join(bits)


def _require(mapping: dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ReferencePriceError(f"{context}: missing required field {key!r}")
    return mapping[key]


def _parse_date(value: Any, context: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ReferencePriceError(f"{context}: bad date {value!r}") from exc


def parse_sources(raw: dict[str, Any]) -> dict[str, ReferenceSource]:
    """Parse and licence-check the ``sources`` block.

    Raises:
        ReferencePriceError: if any source carries a licence that does not
            permit redistribution. The refusal names the licence and the
            source, because the fix is to drop the source or correct the
            field, and both need to know which one it was.
    """
    sources: dict[str, ReferenceSource] = {}
    for key, entry in raw.items():
        context = f"source {key!r}"
        licence = str(_require(entry, "licence", context))
        if licence not in REDISTRIBUTABLE_LICENCES:
            raise ReferencePriceError(
                f"{context}: licence {licence!r} is not redistributable. "
                f"Reference prices must be passable on to anyone who receives "
                f"this dataset; permitted licences are "
                f"{', '.join(sorted(REDISTRIBUTABLE_LICENCES))}. A subscription "
                f"assessment belongs in a local CSV override, not here."
            )
        retrieved = entry.get("retrieved")
        sources[key] = ReferenceSource(
            key=key,
            title=str(_require(entry, "title", context)),
            licence=licence,
            url=entry.get("url"),
            retrieved=_parse_date(retrieved, context) if retrieved else None,
        )
    return sources


@dataclass(frozen=True, slots=True)
class ReferenceDataset:
    """A parsed, licence-checked set of reference prices."""

    generated_at: date
    sources: dict[str, ReferenceSource]
    prices: dict[str, PriceQuote]
    attributions: tuple[str, ...]

    def forms(self) -> frozenset[str]:
        return frozenset(self.prices)


def parse_dataset(raw: dict[str, Any]) -> ReferenceDataset:
    """Parse a reference dataset, refusing anything unusable.

    Every failure here raises rather than skipping the offending entry. A
    quietly dropped price becomes a form the resolver falls through on, and the
    valuation still produces a number -- just a worse one, with nothing in the
    output saying so.
    """
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ReferencePriceError(
            f"unsupported schema_version {version!r}, expected {SCHEMA_VERSION}"
        )
    sources = parse_sources(_require(raw, "sources", "dataset"))
    generated_at = _parse_date(_require(raw, "generated_at", "dataset"), "dataset")

    quotes: dict[str, PriceQuote] = {}
    for form, entry in _require(raw, "prices", "dataset").items():
        context = f"price {form!r}"
        source_key = str(_require(entry, "source", context))
        if source_key not in sources:
            raise ReferencePriceError(
                f"{context}: source {source_key!r} is not declared in sources"
            )
        basis = str(_require(entry, "basis", context))
        if basis not in PRICE_BASES:
            raise ReferencePriceError(
                f"{context}: unknown basis {basis!r}; "
                f"expected one of {', '.join(sorted(PRICE_BASES))}"
            )
        period_start = _parse_date(_require(entry, "period_start", context), context)
        period_end = _parse_date(_require(entry, "period_end", context), context)
        if period_end < period_start:
            raise ReferencePriceError(
                f"{context}: period_end {period_end} precedes period_start "
                f"{period_start}"
            )
        price = float(_require(entry, "price", context))
        if price <= 0:
            raise ReferencePriceError(f"{context}: price must be positive, got {price}")

        source = sources[source_key]
        detail = f"{source.title}, {basis.replace('_', ' ')}"
        if entry.get("series"):
            detail = f"{detail} ({entry['series']})"
        if entry.get("region"):
            detail = f"{detail}, {entry['region']}"

        quotes[form] = PriceQuote(
            form=form,
            price=price,
            currency=str(_require(entry, "currency", context)),
            unit=str(_require(entry, "unit", context)),
            # The window closes on period_end, so that is the earliest date the
            # number can honestly claim. Dating it to publication or to load
            # time would hide the lag the staleness decay exists to price in.
            as_of=period_end,
            source=source_key,
            quality=PriceQuality.REFERENCE,
            source_detail=detail,
            url=source.url,
            period_start=period_start,
            period_end=period_end,
        )

    used = {quote.source for quote in quotes.values()}
    return ReferenceDataset(
        generated_at=generated_at,
        sources=sources,
        prices=quotes,
        attributions=tuple(
            sorted(sources[key].attribution() for key in used if key in sources)
        ),
    )


def load_dataset(path: str | Path) -> ReferenceDataset:
    """Load and validate a reference dataset from ``path``."""
    resolved = Path(path)
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReferencePriceError(f"{resolved}: not valid JSON ({exc})") from exc
    return parse_dataset(raw)


def default_dataset_path() -> Path | None:
    """The dataset ``$BV_REFERENCE_PRICES`` points at, if it is set."""
    configured = os.environ.get(_ENV_DATASET_PATH)
    if not configured:
        return None
    return Path(configured)


class ReferenceProvider(PriceProvider):
    """Serves an operator-supplied dataset of public-agency prices.

    Unlike :class:`~battery_value.market.providers.baseline.BaselineProvider`
    this ships no data of its own. Nothing is bundled because there is nothing
    honest to bundle: inventing plausible figures would produce a valuation
    that looks sourced and is not. With no dataset configured the provider
    reports itself unavailable and the chain falls through to the snapshot.
    """

    key = "reference"
    label = "Public reference series"
    quality = PriceQuality.REFERENCE
    requires_network = False

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else default_dataset_path()
        self._dataset: ReferenceDataset | None = None
        self._load_failed = False

    def dataset(self) -> ReferenceDataset | None:
        """The parsed dataset, loaded once.

        A malformed dataset is reported once and then treated as absent, so one
        bad file degrades the chain to the snapshot instead of failing every
        valuation in the process.
        """
        if self._dataset is not None or self._load_failed or self._path is None:
            return self._dataset
        try:
            self._dataset = load_dataset(self._path)
        except (ReferencePriceError, OSError) as exc:
            logger.warning("reference dataset %s unusable: %s", self._path, exc)
            self._load_failed = True
            return None
        return self._dataset

    def is_available(self) -> bool:
        """Whether a dataset is configured and loadable."""
        return self.dataset() is not None

    def supported_forms(self) -> frozenset[str]:
        dataset = self.dataset()
        return dataset.forms() if dataset else frozenset()

    def fetch(self, form: str) -> PriceQuote | None:
        dataset = self.dataset()
        if dataset is None:
            return None
        return dataset.prices.get(form)

    def attributions(self) -> tuple[str, ...]:
        """Attribution lines for every source actually quoted.

        CC-BY is only satisfied if these travel with the output, so the report
        and certificate layers surface them rather than leaving it to callers.
        """
        dataset = self.dataset()
        return dataset.attributions if dataset else ()

    def describe(self) -> str:
        dataset = self.dataset()
        if dataset is None:
            where = self._path or f"unset ${_ENV_DATASET_PATH}"
            return f"{self.key} ({self.label}) [reference, unavailable: {where}]"
        return (
            f"{self.key} ({self.label}) [reference, available, "
            f"{len(dataset.prices)} form(s), generated {dataset.generated_at}]"
        )
