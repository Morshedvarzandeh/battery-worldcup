"""Price quote types, with provenance attached to every number.

A residual value is only as trustworthy as the prices behind it. Every quote
therefore carries where it came from, when it was struck, and how good it is,
so the final answer can be audited line by line rather than taken on faith.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from ..compounds import get_traded_form
from ..money import Money, normalise_currency
from ..units import MassUnit, parse_mass_unit, to_kg


class PriceQuality(str, Enum):
    """How much weight a quote deserves."""

    LIVE = "live"
    """Same-day exchange settlement or spot assessment."""

    BENCHMARK = "benchmark"
    """A subscription price index (Fastmarkets, Benchmark Minerals, SMM, Argus)."""

    DELAYED = "delayed"
    """Delayed exchange data, or a futures contract used as a proxy for physical."""

    MANUAL = "manual"
    """Supplied by the caller, e.g. an offtake price the holder has been quoted."""

    REFERENCE = "reference"
    """A public-agency series, e.g. the World Bank Pink Sheet or USGS.

    Redistributable and citable, which the subscription assessments are not,
    but published monthly or annually as an average over a period rather than
    as a spot price. Good enough to anchor a valuation, never good enough to
    settle a trade on.
    """

    BASELINE = "baseline"
    """The bundled dated snapshot. Always available, never current."""

    @property
    def base_confidence(self) -> float:
        """Confidence before any staleness penalty."""
        return _BASE_CONFIDENCE[self]


_BASE_CONFIDENCE: dict[PriceQuality, float] = {
    PriceQuality.LIVE: 1.00,
    PriceQuality.BENCHMARK: 0.95,
    PriceQuality.MANUAL: 0.90,
    PriceQuality.DELAYED: 0.82,
    PriceQuality.REFERENCE: 0.70,
    PriceQuality.BASELINE: 0.55,
}

# A quote loses this much confidence per day of age, floored at _MIN_CONFIDENCE.
# Battery metals can move 10%+ in a month, so a quarter-old number is close to
# worthless for a live valuation even if it was authoritative when struck.
_CONFIDENCE_DECAY_PER_DAY = 0.004
_MIN_CONFIDENCE = 0.15


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """A price for one traded form, per unit mass of that form."""

    form: str
    """Traded-form key, e.g. ``lithium_carbonate``. See :mod:`compounds`."""

    price: float
    currency: str
    unit: MassUnit
    as_of: date
    source: str
    quality: PriceQuality
    source_detail: str = ""
    url: str | None = None

    # A public series publishes an average over a period, not a price struck on
    # a day. Recording the window keeps "the July average" from being read as
    # "the price on 31 July": same as_of, materially different claim. Both NULL
    # for a spot quote, which is what every exchange and assessment provider
    # returns.
    period_start: date | None = None
    period_end: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", normalise_currency(self.currency))
        object.__setattr__(self, "unit", parse_mass_unit(self.unit))
        object.__setattr__(self, "price", float(self.price))

    @property
    def price_per_kg(self) -> float:
        """Price per kg of the traded form, in :attr:`currency`."""
        return self.price / to_kg(1.0, self.unit)

    def price_per_kg_contained(self) -> float:
        """Price per kg of the *contained payable element*.

        Lithium carbonate at EUR 12,000/t is EUR 12/kg of carbonate but
        EUR 63.88/kg of contained lithium, because a kg of Li2CO3 is only
        18.79% lithium.
        """
        return self.price_per_kg / get_traded_form(self.form).contained_fraction()

    def money_per_kg_contained(self) -> Money:
        """:meth:`price_per_kg_contained` as a :class:`Money`."""
        return Money(self.price_per_kg_contained(), self.currency)

    def staleness_days(self, today: date | None = None) -> int:
        """Whole days between :attr:`as_of` and ``today`` (never negative)."""
        reference = today or date.today()
        return max((reference - self.as_of).days, 0)

    def confidence(self, today: date | None = None) -> float:
        """Confidence in this quote, 0-1, after the staleness penalty."""
        decayed = self.quality.base_confidence - (
            _CONFIDENCE_DECAY_PER_DAY * self.staleness_days(today)
        )
        return max(decayed, _MIN_CONFIDENCE)

    def is_stale(self, max_age_days: int = 45, today: date | None = None) -> bool:
        """Whether this quote is older than ``max_age_days``."""
        return self.staleness_days(today) > max_age_days

    def in_currency(self, currency: str, rate: float) -> PriceQuote:
        """A copy converted with ``rate`` units of ``currency`` per unit of self."""
        target = normalise_currency(currency)
        if target == self.currency:
            return self
        return PriceQuote(
            form=self.form,
            price=self.price * rate,
            currency=target,
            unit=self.unit,
            as_of=self.as_of,
            source=self.source,
            quality=self.quality,
            source_detail=(
                f"{self.source_detail} (converted from {self.currency} at {rate:.4f})"
                if self.source_detail
                else f"converted from {self.currency} at {rate:.4f}"
            ),
            url=self.url,
            period_start=self.period_start,
            period_end=self.period_end,
        )

    @property
    def is_period_average(self) -> bool:
        """Whether this quote averages a window rather than pricing a day."""
        return self.period_start is not None and self.period_end is not None

    def describe(self) -> str:
        """One-line human-readable provenance string."""
        label = get_traded_form(self.form).label
        window = (
            f" [avg {self.period_start.isoformat()}..{self.period_end.isoformat()}]"
            if self.is_period_average
            else ""
        )
        return (
            f"{label}: {self.price:,.2f} {self.currency}/{self.unit.value} "
            f"as of {self.as_of.isoformat()}{window} "
            f"[{self.quality.value}] via {self.source}"
        )


@dataclass(frozen=True, slots=True)
class PriceSet:
    """The full set of quotes used for one valuation, with aggregate provenance."""

    quotes: dict[str, PriceQuote]
    currency: str
    resolved_at: date
    missing: tuple[str, ...] = ()

    def get(self, form: str) -> PriceQuote | None:
        """Quote for a traded form, if one was resolved."""
        return self.quotes.get(form)

    @property
    def confidence(self) -> float:
        """Mean confidence across all quotes, 0 when empty."""
        if not self.quotes:
            return 0.0
        return sum(
            quote.confidence(self.resolved_at) for quote in self.quotes.values()
        ) / len(self.quotes)

    @property
    def oldest_as_of(self) -> date | None:
        """The oldest quote date in the set."""
        if not self.quotes:
            return None
        return min(quote.as_of for quote in self.quotes.values())

    def sources_used(self) -> dict[str, int]:
        """Count of quotes contributed by each provider."""
        counts: dict[str, int] = {}
        for quote in self.quotes.values():
            counts[quote.source] = counts.get(quote.source, 0) + 1
        return counts

    def stale_forms(self, max_age_days: int = 45) -> tuple[str, ...]:
        """Traded forms whose quote is older than ``max_age_days``."""
        return tuple(
            form
            for form, quote in self.quotes.items()
            if quote.is_stale(max_age_days, self.resolved_at)
        )

    def provenance_lines(self) -> list[str]:
        """Auditable one-line description of every quote used."""
        return [quote.describe() for quote in self.quotes.values()]


def days_ago(days: int, today: date | None = None) -> date:
    """Helper for provider implementations and tests."""
    return (today or date.today()) - timedelta(days=days)
