"""Walk the provider chain and produce a currency-normalised set of prices."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..errors import NoPriceAvailableError
from .cache import PriceCache
from .fx import FxConverter
from .providers.base import PriceProvider
from .providers.baseline import BaselineProvider
from .providers.csv_override import CsvOverrideProvider
from .providers.exchange import metals_api, yahoo_provider
from .providers.manual import ManualProvider
from .types import PriceQuote, PriceSet

logger = logging.getLogger(__name__)

_ENV_CSV_PATH = "BV_PRICE_CSV"


@dataclass
class PriceResolver:
    """Resolves traded forms to quotes by trying providers in order.

    The chain is ordered best-source-first. The first provider that answers
    wins, so a caller-supplied offtake price beats a subscription index, which
    beats an exchange proxy, which beats the bundled snapshot.
    """

    providers: list[PriceProvider]
    currency: str = "EUR"
    fx: FxConverter = field(default_factory=FxConverter)
    today: date | None = None

    def available_providers(self) -> list[PriceProvider]:
        """Providers that report themselves usable right now."""
        return [provider for provider in self.providers if provider.is_available()]

    def resolve(self, form: str) -> PriceQuote | None:
        """Best available quote for ``form``, converted to :attr:`currency`."""
        for provider in self.providers:
            if not provider.is_available():
                continue
            quote = provider.fetch_safe(form)
            if quote is None:
                continue
            return self._to_target_currency(quote)
        return None

    def require(self, form: str) -> PriceQuote:
        """Like :meth:`resolve` but raises when nothing can quote ``form``."""
        quote = self.resolve(form)
        if quote is None:
            raise NoPriceAvailableError(
                form, [provider.key for provider in self.available_providers()]
            )
        return quote

    def resolve_many(self, forms: Iterable[str]) -> PriceSet:
        """Resolve every form, recording which ones could not be priced."""
        quotes: dict[str, PriceQuote] = {}
        missing: list[str] = []
        for form in dict.fromkeys(forms):  # de-duplicate, preserve order
            quote = self.resolve(form)
            if quote is None:
                missing.append(form)
                logger.warning("no provider could quote %s", form)
            else:
                quotes[form] = quote
        return PriceSet(
            quotes=quotes,
            currency=self.currency,
            resolved_at=self.today or date.today(),
            missing=tuple(missing),
        )

    def _to_target_currency(self, quote: PriceQuote) -> PriceQuote:
        if quote.currency == self.currency:
            return quote
        try:
            rate = self.fx.factor(quote.currency, self.currency)
        except Exception as exc:  # noqa: BLE001 - never fail a valuation on FX
            logger.warning(
                "FX conversion %s->%s failed (%s); keeping original currency",
                quote.currency,
                self.currency,
                exc,
            )
            return quote
        return quote.in_currency(self.currency, rate)

    def describe_chain(self) -> list[str]:
        """One line per provider, for diagnostics and the API's /providers route."""
        return [provider.describe() for provider in self.providers]


def build_resolver(
    *,
    currency: str = "EUR",
    manual: ManualProvider | dict[str, float] | None = None,
    csv_path: str | Path | None = None,
    offline: bool = False,
    cache: PriceCache | None = None,
    today: date | None = None,
    extra_providers: Iterable[PriceProvider] = (),
) -> PriceResolver:
    """Assemble the standard provider chain.

    Ordering, best first:

    1. ``manual``   - a price the holder has actually been quoted.
    2. ``csv``      - subscription assessments exported locally (Fastmarkets, SMM...).
    3. ``metals_api`` - a keyed commercial API, when ``METALS_API_KEY`` is set.
    4. ``yahoo``    - free futures proxies for the exchange-traded base metals.
    5. ``baseline`` - the bundled snapshot, so a result always exists.

    Args:
        currency: Currency every quote is converted into.
        manual: Caller-supplied prices, or a plain ``{form: price_per_tonne}`` map.
        csv_path: Local assessment CSV. Defaults to ``$BV_PRICE_CSV``.
        offline: Skip all network providers, including the ECB FX feed.
        extra_providers: Inserted ahead of the baseline provider.
    """
    providers: list[PriceProvider] = []

    if manual is not None:
        if isinstance(manual, ManualProvider):
            providers.append(manual)
        else:
            supplied = ManualProvider()
            for form, price in manual.items():
                supplied.add(form, price, currency=currency)
            providers.append(supplied)

    resolved_csv = csv_path or os.environ.get(_ENV_CSV_PATH)
    if resolved_csv:
        providers.append(CsvOverrideProvider(resolved_csv))

    if not offline:
        providers.append(metals_api(cache=cache))
        providers.append(yahoo_provider(cache=cache))

    providers.extend(extra_providers)
    providers.append(BaselineProvider())

    return PriceResolver(
        providers=providers,
        currency=currency,
        fx=FxConverter(offline=offline, cache=cache),
        today=today,
    )
