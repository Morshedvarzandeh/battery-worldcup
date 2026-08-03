"""The bundled snapshot provider: always available, never current."""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from importlib import resources
from typing import Any

from ...money import Money
from ..types import PriceQuality, PriceQuote
from .base import PriceProvider

_DATA_PACKAGE = "battery_worldcup.market.data"
_DATA_FILE = "baseline_prices.json"


@lru_cache(maxsize=1)
def load_baseline_data() -> dict[str, Any]:
    """Load and cache the bundled baseline price file."""
    with resources.files(_DATA_PACKAGE).joinpath(_DATA_FILE).open(
        encoding="utf-8"
    ) as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def baseline_snapshot_date() -> date:
    """The date the bundled snapshot represents."""
    return date.fromisoformat(load_baseline_data()["snapshot_date"])


class BaselineProvider(PriceProvider):
    """Serves the dated snapshot shipped with the package.

    This is the safety net that makes the module usable with no configuration
    at all. Quotes are tagged :attr:`PriceQuality.BASELINE` and their confidence
    decays daily, so a valuation built purely on baseline data reports low
    confidence rather than false precision.
    """

    key = "baseline"
    label = "Bundled snapshot"
    quality = PriceQuality.BASELINE
    requires_network = False

    def supported_forms(self) -> frozenset[str]:
        """Every material in the bundled snapshot."""
        return frozenset(load_baseline_data()["materials"])

    def fetch(self, form: str) -> PriceQuote | None:
        """Quote ``form`` from the snapshot."""
        entry = load_baseline_data()["materials"].get(form)
        if entry is None:
            return None
        return PriceQuote(
            form=form,
            price=float(entry["price"]),
            currency=entry["currency"],
            unit=entry["unit"],
            as_of=baseline_snapshot_date(),
            source=self.key,
            quality=self.quality,
            source_detail=entry.get("reference", "bundled snapshot"),
        )

    def annual_volatility(self, form: str) -> float:
        """Indicative annualised volatility, used by the sensitivity analysis."""
        entry = load_baseline_data()["materials"].get(form, {})
        return float(entry.get("volatility_annual", 0.25))


def system_price(key: str) -> Money:
    """A whole-system reference price, e.g. ``new_pack_price`` in USD/kWh.

    Used by the reuse and second-life pathways, which are valued against what a
    comparable new system costs rather than against contained metal.
    """
    systems = load_baseline_data()["systems"]
    entry = systems.get(key)
    if entry is None:
        available = ", ".join(k for k in systems if k != "notes")
        raise KeyError(f"unknown system price {key!r}; available: {available}")
    return Money(float(entry["price"]), entry["currency"])


def system_price_reference(key: str) -> str:
    """The sourcing note behind a system reference price."""
    entry = load_baseline_data()["systems"].get(key, {})
    return entry.get("reference", "")
