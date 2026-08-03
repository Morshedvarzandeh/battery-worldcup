"""Free exchange-derived providers.

Coverage here is honest but partial, and that partiality is the whole point:
copper, aluminium and lead are exchange-traded and therefore freely quotable,
while the materials that actually drive battery value -- lithium carbonate,
nickel and cobalt sulphate -- are assessed by subscription price reporting
agencies and have no free live feed. The resolver fills those from the CSV
override, a keyed vendor, or the bundled snapshot.

Futures contracts are a proxy for physical metal, not the same thing, so
everything sourced here is tagged :attr:`PriceQuality.DELAYED`.
"""

from __future__ import annotations

from ...units import MassUnit
from ..cache import PriceCache
from ..types import PriceQuality
from .http_json import HttpJsonProvider, SymbolSpec

YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
)

# COMEX copper trades in USD per pound; COMEX aluminium in USD per metric tonne.
# Getting these units wrong silently inflates copper value by ~2,200x, so they
# are declared per symbol rather than assumed.
YAHOO_SYMBOLS: dict[str, SymbolSpec] = {
    "copper_metal": SymbolSpec(symbol="HG=F", unit=MassUnit.POUND, currency="USD"),
    "aluminium_metal": SymbolSpec(symbol="ALI=F", unit=MassUnit.TONNE, currency="USD"),
    "steel_scrap": SymbolSpec(symbol="HRC=F", unit=MassUnit.SHORT_TON, currency="USD"),
}


def yahoo_provider(
    *,
    cache: PriceCache | None = None,
    timeout: float = 12.0,
    client=None,
) -> HttpJsonProvider:
    """Free, unauthenticated futures quotes for the exchange-traded metals.

    Yahoo's chart endpoint is an undocumented public API: fine as a fallback,
    not something to build a commercial quote on. It covers only the base
    metals, which are a minority of a lithium-ion pack's material value.
    """
    return HttpJsonProvider(
        provider_key="yahoo",
        provider_label="Yahoo Finance futures",
        provider_quality=PriceQuality.DELAYED,
        url_template=YAHOO_CHART_URL,
        symbols=YAHOO_SYMBOLS,
        price_path="chart.result.0.meta.regularMarketPrice",
        date_path="chart.result.0.meta.regularMarketTime",
        headers={"User-Agent": "Mozilla/5.0 (compatible; battery-value/0.1)"},
        cache=cache,
        timeout=timeout,
        client=client,
    )


# metals-api.com returns rates as "units of metal per 1 unit of base currency",
# so every value needs inverting to become a price. LME base metals are quoted
# per metric tonne under LME- prefixed symbols.
METALS_API_SYMBOLS: dict[str, SymbolSpec] = {
    "nickel_metal": SymbolSpec("LME-NI", MassUnit.TONNE, "USD", invert=True),
    "copper_metal": SymbolSpec("LME-CU", MassUnit.TONNE, "USD", invert=True),
    "aluminium_metal": SymbolSpec("LME-ALU", MassUnit.TONNE, "USD", invert=True),
    "lead_metal": SymbolSpec("LME-PB", MassUnit.TONNE, "USD", invert=True),
    "cobalt_metal": SymbolSpec("LME-COBALT", MassUnit.TONNE, "USD", invert=True),
    "lithium_carbonate": SymbolSpec("LITHIUM", MassUnit.TONNE, "USD", invert=True),
}


def metals_api_provider(
    *,
    cache: PriceCache | None = None,
    timeout: float = 12.0,
    client=None,
) -> HttpJsonProvider:
    """metals-api.com, activated by setting ``METALS_API_KEY``.

    A paid freemium vendor with genuine LME coverage including nickel and
    cobalt. Verify the returned symbol units against your own plan before
    relying on it: vendors change symbol conventions between tiers.
    """
    return HttpJsonProvider(
        provider_key="metals_api",
        provider_label="metals-api.com",
        provider_quality=PriceQuality.LIVE,
        url_template=(
            "https://metals-api.com/api/latest"
            "?access_key=${METALS_API_KEY}&base=USD&symbols={symbol}"
        ),
        symbols=METALS_API_SYMBOLS,
        price_path="rates.{symbol}",
        date_path="date",
        required_env=("METALS_API_KEY",),
        cache=cache,
        timeout=timeout,
        client=client,
    )


class MetalsApiProvider(HttpJsonProvider):
    """metals-api.com, whose price path is symbol-dependent."""

    def fetch(self, form: str):  # type: ignore[override]
        """Resolve the symbol into the price path before delegating."""
        spec = self.symbols.get(form)
        if spec is None:
            return None
        original = self.price_path
        try:
            self.price_path = original.replace("{symbol}", spec.symbol)
            return super().fetch(form)
        finally:
            self.price_path = original


def metals_api(
    *, cache: PriceCache | None = None, timeout: float = 12.0, client=None
) -> MetalsApiProvider:
    """Build the metals-api.com provider with its symbol-aware price path."""
    template = metals_api_provider(cache=cache, timeout=timeout, client=client)
    return MetalsApiProvider(
        provider_key=template.provider_key,
        provider_label=template.provider_label,
        provider_quality=template.provider_quality,
        url_template=template.url_template,
        symbols=template.symbols,
        price_path=template.price_path,
        date_path=template.date_path,
        required_env=template.required_env,
        cache=cache,
        timeout=timeout,
        client=client,
    )
