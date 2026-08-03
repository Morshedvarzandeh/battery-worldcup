"""The price-provider contract.

Providers are deliberately dumb: they either return a quote for a traded form
or return ``None``. All chaining, currency conversion, caching and fallback
logic lives in :mod:`battery_value.market.resolver`, so adding a new data
source means implementing one method.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from ..types import PriceQuality, PriceQuote

logger = logging.getLogger(__name__)


class PriceProvider(ABC):
    """A source of material prices."""

    key: ClassVar[str] = "abstract"
    label: ClassVar[str] = "Abstract provider"
    quality: ClassVar[PriceQuality] = PriceQuality.BASELINE
    requires_network: ClassVar[bool] = False
    requires_credentials: ClassVar[bool] = False

    def is_available(self) -> bool:
        """Whether this provider can be used right now.

        Providers needing an API key report unavailable when it is missing, so
        the resolver can skip them without raising.
        """
        return True

    @abstractmethod
    def supported_forms(self) -> frozenset[str]:
        """Traded-form keys this provider can quote."""

    @abstractmethod
    def fetch(self, form: str) -> PriceQuote | None:
        """Return a quote for ``form``, or ``None`` if unavailable.

        Implementations must not raise for ordinary failures (network error,
        unknown symbol, rate limit); they should log and return ``None`` so the
        resolver falls through to the next provider.
        """

    def fetch_safe(self, form: str) -> PriceQuote | None:
        """:meth:`fetch` with a blanket guard, used by the resolver."""
        if form not in self.supported_forms():
            return None
        try:
            return self.fetch(form)
        except Exception as exc:  # noqa: BLE001 - one bad provider must not break the chain
            logger.warning(
                "provider %s failed for %s: %s", self.key, form, exc, exc_info=False
            )
            return None

    def describe(self) -> str:
        """Human-readable one-liner for diagnostics."""
        status = "available" if self.is_available() else "unavailable"
        return f"{self.key} ({self.label}) [{self.quality.value}, {status}]"
