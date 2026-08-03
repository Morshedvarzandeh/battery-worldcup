"""Currency conversion, anchored on the ECB's free daily reference rates.

The ECB publishes euro foreign-exchange reference rates as a small XML file
with no key, no quota and no licence restriction, which makes it the right
default for a module that has to work for anyone who clones the repo. When it
is unreachable the bundled fallback rates keep the valuation running, clearly
marked as such.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from xml.etree import ElementTree

import httpx

from ..errors import MarketDataError
from ..money import Money, normalise_currency
from .cache import PriceCache, default_cache

logger = logging.getLogger(__name__)

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_ECB_NAMESPACE = {"ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}

BASE_CURRENCY = "EUR"
_FX_CACHE_KEY = "fx:ecb-daily"
_FX_CACHE_TTL_SECONDS = 6 * 60 * 60


class FxError(MarketDataError):
    """A currency conversion could not be performed."""


@dataclass(frozen=True, slots=True)
class FxRates:
    """Foreign-currency-per-EUR rates for a single day."""

    rates: dict[str, float]
    as_of: date
    source: str
    is_fallback: bool = False

    def rate(self, currency: str) -> float:
        """Units of ``currency`` per 1 EUR."""
        code = normalise_currency(currency)
        if code == BASE_CURRENCY:
            return 1.0
        try:
            return self.rates[code]
        except KeyError:
            raise FxError(
                f"no FX rate for {code}; available: {', '.join(sorted(self.rates))}"
            ) from None

    def factor(self, source: str, target: str) -> float:
        """Multiplier converting an amount in ``source`` into ``target``."""
        source_code = normalise_currency(source)
        target_code = normalise_currency(target)
        if source_code == target_code:
            return 1.0
        # Cross-rate through the EUR base: X -> EUR -> Y.
        return self.rate(target_code) / self.rate(source_code)

    def convert(self, amount: float, source: str, target: str) -> float:
        """Convert a raw amount between currencies."""
        return amount * self.factor(source, target)

    def convert_money(self, money: Money, target: str) -> Money:
        """Convert a :class:`Money` into ``target``."""
        return Money(self.convert(money.amount, money.currency, target), target)

    @property
    def currencies(self) -> tuple[str, ...]:
        """Every currency this rate set can convert."""
        return tuple(sorted({BASE_CURRENCY, *self.rates}))


def parse_ecb_xml(payload: str) -> FxRates:
    """Parse the ECB daily reference-rate XML document."""
    root = ElementTree.fromstring(payload)

    day_node = root.find(".//ecb:Cube[@time]", _ECB_NAMESPACE)
    if day_node is None:
        raise FxError("ECB response contained no dated Cube element")

    as_of = date.fromisoformat(day_node.attrib["time"])
    rates: dict[str, float] = {BASE_CURRENCY: 1.0}
    for node in day_node.findall("ecb:Cube", _ECB_NAMESPACE):
        currency = node.attrib.get("currency")
        rate = node.attrib.get("rate")
        if currency and rate:
            rates[normalise_currency(currency)] = float(rate)

    if len(rates) <= 1:
        raise FxError("ECB response contained no usable rates")

    return FxRates(rates=rates, as_of=as_of, source="ecb", is_fallback=False)


def fetch_ecb_rates(
    *, timeout: float = 10.0, cache: PriceCache | None = None
) -> FxRates:
    """Fetch today's ECB reference rates, using the cache when warm."""
    cache = cache if cache is not None else default_cache()

    cached = cache.get(_FX_CACHE_KEY, ttl_seconds=_FX_CACHE_TTL_SECONDS)
    if cached is not None:
        return FxRates(
            rates={k: float(v) for k, v in cached["rates"].items()},
            as_of=date.fromisoformat(cached["as_of"]),
            source=cached.get("source", "ecb (cached)"),
            is_fallback=False,
        )

    response = httpx.get(ECB_DAILY_URL, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    parsed = parse_ecb_xml(response.text)

    cache.set(
        _FX_CACHE_KEY,
        {
            "rates": parsed.rates,
            "as_of": parsed.as_of.isoformat(),
            "source": parsed.source,
        },
    )
    return parsed


def fallback_rates() -> FxRates:
    """The bundled rates, used when the ECB feed cannot be reached."""
    # Imported here to avoid a circular import at module load.
    from .providers.baseline import load_baseline_data

    data = load_baseline_data()["fx_fallback"]
    return FxRates(
        rates={normalise_currency(k): float(v) for k, v in data["rates"].items()},
        as_of=date.fromisoformat(data["as_of"]),
        source="bundled fallback",
        is_fallback=True,
    )


def get_fx_rates(
    *, offline: bool = False, timeout: float = 10.0, cache: PriceCache | None = None
) -> FxRates:
    """Best available FX rates, degrading to the bundled snapshot on failure."""
    if offline:
        return fallback_rates()
    try:
        return fetch_ecb_rates(timeout=timeout, cache=cache)
    except Exception as exc:  # noqa: BLE001 - any failure must fall back, not crash
        logger.warning("ECB FX fetch failed (%s); using bundled fallback rates", exc)
        return fallback_rates()


@dataclass
class FxConverter:
    """Lazily-loaded FX access for the price resolver."""

    offline: bool = False
    timeout: float = 10.0
    cache: PriceCache | None = None
    _rates: FxRates | None = field(default=None, init=False, repr=False)

    @property
    def rates(self) -> FxRates:
        """The rate set, fetched on first use."""
        if self._rates is None:
            self._rates = get_fx_rates(
                offline=self.offline, timeout=self.timeout, cache=self.cache
            )
        return self._rates

    def factor(self, source: str, target: str) -> float:
        """Multiplier converting ``source`` into ``target``."""
        return self.rates.factor(source, target)
