"""Caller-supplied prices.

The highest-value input a holder can give us is the price they have actually
been quoted. A recycler's offtake offer beats any index, so manual quotes sit
at the front of the resolver chain.
"""

from __future__ import annotations

from datetime import date

from ...units import MassUnit
from ..types import PriceQuality, PriceQuote
from .base import PriceProvider


class ManualProvider(PriceProvider):
    """Prices injected in memory, e.g. from an API request body."""

    key = "manual"
    label = "Caller-supplied prices"
    quality = PriceQuality.MANUAL
    requires_network = False

    def __init__(self, quotes: dict[str, PriceQuote] | None = None) -> None:
        self._quotes: dict[str, PriceQuote] = dict(quotes or {})

    def add(
        self,
        form: str,
        price: float,
        currency: str = "EUR",
        unit: str | MassUnit = MassUnit.TONNE,
        as_of: date | None = None,
        source_detail: str = "supplied by caller",
    ) -> ManualProvider:
        """Register a price. Returns ``self`` so calls can be chained."""
        self._quotes[form] = PriceQuote(
            form=form,
            price=price,
            currency=currency,
            unit=unit,
            as_of=as_of or date.today(),
            source=self.key,
            quality=self.quality,
            source_detail=source_detail,
        )
        return self

    def supported_forms(self) -> frozenset[str]:
        """Forms explicitly registered by the caller."""
        return frozenset(self._quotes)

    def is_available(self) -> bool:
        """Available only when at least one price has been registered."""
        return bool(self._quotes)

    def fetch(self, form: str) -> PriceQuote | None:
        """Return the registered quote for ``form``."""
        return self._quotes.get(form)
