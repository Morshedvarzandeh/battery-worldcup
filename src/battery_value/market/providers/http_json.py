"""A configurable provider for any HTTP endpoint that returns JSON.

Battery-metal price vendors all expose broadly the same thing -- a JSON
document with a number in it -- behind incompatible URL shapes, auth schemes
and units. Rather than hard-code a class per vendor, this provider is driven by
a small declarative spec, so pointing the module at a new feed is configuration
rather than code.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import httpx

from ...units import MassUnit
from ..cache import PriceCache, default_cache
from ..types import PriceQuality, PriceQuote
from .base import PriceProvider

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class JsonPathError(ValueError):
    """A configured JSON path did not resolve in the response."""


def extract_path(payload: Any, path: str) -> Any:
    """Walk a dotted JSON path, where integer segments index into lists.

    >>> extract_path({"chart": {"result": [{"price": 4.5}]}}, "chart.result.0.price")
    4.5
    """
    current = payload
    for segment in path.split("."):
        if segment == "":
            continue
        try:
            if isinstance(current, list):
                current = current[int(segment)]
            else:
                current = current[segment]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise JsonPathError(
                f"could not resolve {path!r} (failed at segment {segment!r})"
            ) from exc
    return current


def expand_env(text: str) -> str:
    """Replace ``${VAR}`` placeholders with environment values."""
    return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), text)


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    """How one traded form maps onto a vendor's symbol and units."""

    symbol: str
    unit: MassUnit = MassUnit.TONNE
    currency: str = "USD"
    scale: float = 1.0
    """Multiplier applied to the raw value, for feeds quoting in cents etc."""

    invert: bool = False
    """Set when the feed returns units-per-currency instead of currency-per-unit."""


@dataclass
class HttpJsonProvider(PriceProvider):
    """Fetch prices from a JSON HTTP endpoint described by a spec.

    Args:
        provider_key: Identifier recorded on every quote's provenance.
        url_template: URL with ``{symbol}`` and ``${ENV_VAR}`` placeholders.
        symbols: Traded-form key -> :class:`SymbolSpec`.
        price_path: Dotted JSON path to the numeric price.
        date_path: Optional dotted path to a timestamp or ISO date.
        required_env: Environment variables that must be set to be available.
    """

    provider_key: str
    url_template: str
    symbols: dict[str, SymbolSpec]
    price_path: str
    date_path: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    required_env: tuple[str, ...] = ()
    provider_label: str = "HTTP JSON feed"
    provider_quality: PriceQuality = PriceQuality.DELAYED
    timeout: float = 12.0
    cache: PriceCache | None = None
    cache_ttl_seconds: int = 30 * 60
    client: httpx.Client | None = None

    requires_network = True

    def __post_init__(self) -> None:
        self.key = self.provider_key
        self.label = self.provider_label
        self.quality = self.provider_quality
        self.requires_credentials = bool(self.required_env)

    def is_available(self) -> bool:
        """Available when every required credential env var is populated."""
        return all(os.environ.get(name) for name in self.required_env)

    def supported_forms(self) -> frozenset[str]:
        """Forms with a configured symbol."""
        return frozenset(self.symbols)

    def fetch(self, form: str) -> PriceQuote | None:
        """Fetch and parse a quote for ``form``."""
        spec = self.symbols.get(form)
        if spec is None:
            return None

        cache = self.cache if self.cache is not None else default_cache()
        cache_key = f"price:{self.key}:{form}"

        cached = cache.get(cache_key, ttl_seconds=self.cache_ttl_seconds)
        if cached is not None:
            return self._quote_from_parts(
                form, spec, float(cached["price"]), date.fromisoformat(cached["as_of"])
            )

        payload = self._request(spec)
        if payload is None:
            return None

        try:
            raw_price = float(extract_path(payload, self.price_path))
        except (JsonPathError, TypeError, ValueError) as exc:
            logger.warning("%s: bad price payload for %s: %s", self.key, form, exc)
            return None

        if raw_price <= 0:
            logger.warning("%s: non-positive price for %s: %s", self.key, form, raw_price)
            return None

        price = (1.0 / raw_price if spec.invert else raw_price) * spec.scale
        as_of = self._extract_date(payload)

        cache.set(cache_key, {"price": price, "as_of": as_of.isoformat()})
        return self._quote_from_parts(form, spec, price, as_of)

    def _request(self, spec: SymbolSpec) -> Any | None:
        url = expand_env(self.url_template).format(symbol=spec.symbol)
        headers = {name: expand_env(value) for name, value in self.headers.items()}
        try:
            if self.client is not None:
                response = self.client.get(url, headers=headers, timeout=self.timeout)
            else:
                response = httpx.get(
                    url,
                    headers=headers,
                    timeout=self.timeout,
                    follow_redirects=True,
                )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("%s: request failed for %s: %s", self.key, spec.symbol, exc)
            return None

    def _extract_date(self, payload: Any) -> date:
        if not self.date_path:
            return date.today()
        try:
            raw = extract_path(payload, self.date_path)
        except JsonPathError:
            return date.today()
        return _coerce_date(raw)

    def _quote_from_parts(
        self, form: str, spec: SymbolSpec, price: float, as_of: date
    ) -> PriceQuote:
        return PriceQuote(
            form=form,
            price=price,
            currency=spec.currency,
            unit=spec.unit,
            as_of=as_of,
            source=self.key,
            quality=self.quality,
            source_detail=f"{self.label} symbol {spec.symbol}",
        )


def _coerce_date(raw: Any) -> date:
    """Best-effort conversion of a vendor timestamp into a date."""
    if isinstance(raw, (int, float)):
        # Feeds send seconds or milliseconds since the epoch.
        seconds = float(raw) / 1000.0 if float(raw) > 1e11 else float(raw)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return date.today()
    if isinstance(raw, str):
        text = raw.strip().replace("Z", "+00:00")
        for parser in (date.fromisoformat, lambda t: datetime.fromisoformat(t).date()):
            try:
                return parser(text)  # type: ignore[operator]
            except ValueError:
                continue
        # Some feeds send a bare date inside a longer string.
        match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if match:
            try:
                return date.fromisoformat(match.group(0))
            except ValueError:
                pass
    return date.today()
