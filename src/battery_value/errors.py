"""Exception hierarchy for the residual-value module."""

from __future__ import annotations


class BatteryValueError(Exception):
    """Base class for every error raised by this package."""


class PassportError(BatteryValueError):
    """Raised when passport data cannot be read, parsed or trusted."""


class UnknownCarrierError(PassportError):
    """The scanned QR/data-carrier payload matched no known passport format."""


class PassportValidationError(PassportError):
    """A passport was parsed but is missing fields the valuation requires."""


class MarketDataError(BatteryValueError):
    """Base class for price-sourcing failures."""


class NoPriceAvailableError(MarketDataError):
    """No provider in the chain could quote a material and no fallback existed."""

    def __init__(self, material: str, tried: list[str] | None = None) -> None:
        self.material = material
        self.tried = tried or []
        detail = f" (tried: {', '.join(self.tried)})" if self.tried else ""
        super().__init__(f"no price available for {material!r}{detail}")


class ProviderError(MarketDataError):
    """A single provider failed. The resolver catches these and moves on."""


class UnitError(BatteryValueError):
    """An impossible or unsupported unit conversion was requested."""


class ValuationError(BatteryValueError):
    """The valuation engine could not produce a result."""
