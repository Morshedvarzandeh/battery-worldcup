"""Tunable valuation assumptions, in one place.

Every judgement call the engine makes is a named field here rather than a
number buried in a formula, so an operator can calibrate the model to their own
market without editing logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ReuseAssumptions:
    """Resale as a replacement traction pack."""

    minimum_soh: float = 0.75
    maximum_age_years: float = 12.0
    fallback_oem_price_eur_per_kwh: float = 480.0
    """Used when the pack model is unknown, so no model-specific price exists."""

    used_vs_new_discount: float = 0.45
    """A used pack sells for roughly this share of the new OEM part price."""

    health_exponent: float = 1.5
    """Value falls faster than state of health does; >1 makes the curve convex."""

    age_penalty_per_year: float = 0.05
    minimum_age_factor: float = 0.40


@dataclass(frozen=True, slots=True)
class SecondLifeAssumptions:
    """Repurposing into stationary storage."""

    minimum_soh: float = 0.60
    end_of_life_soh: float = 0.50
    usable_dod_window: float = 0.90
    """Second-life systems limit depth of discharge to protect a tired pack."""

    new_system_cycle_life: int = 6000
    """Cycle life of the new turnkey BESS a repurposed pack competes against."""

    maximum_age_years: float = 15.0


@dataclass(frozen=True, slots=True)
class PartsOutAssumptions:
    """Dismantling and selling components individually."""

    minimum_soh: float = 0.50
    scrap_payable_fraction: float = 0.60
    """Share of metal value paid for clean, sorted enclosure and harness scrap."""

    module_market_depth: float = 0.85
    """Not every module finds a buyer; this is the realistic sell-through rate."""


@dataclass(frozen=True, slots=True)
class RecyclingAssumptions:
    """Material recovery."""

    prefer_process: str | None = None
    """Force a specific process key instead of picking the highest-value one."""

    include_pilot_processes: bool = False


@dataclass(frozen=True, slots=True)
class ValuationConfig:
    """Everything the engine needs beyond the passport and the market."""

    currency: str = "EUR"
    reuse: ReuseAssumptions = field(default_factory=ReuseAssumptions)
    second_life: SecondLifeAssumptions = field(default_factory=SecondLifeAssumptions)
    parts_out: PartsOutAssumptions = field(default_factory=PartsOutAssumptions)
    recycling: RecyclingAssumptions = field(default_factory=RecyclingAssumptions)

    assumed_soh_when_unknown: float = 0.80
    """Used only when neither health, cycles nor age are available."""

    calendar_fade_per_year: float = 0.023
    """Annual capacity fade used to estimate health from age alone."""

    sensitivity_price_shock: float = 0.25
    """Relative material-price move used for the low/high valuation range."""

    sensitivity_soh_shock: float = 0.05
    """Absolute state-of-health move, in fraction points, for the same range."""

    minimum_confidence_to_quote: float = 0.35
    """Below this, the result is reported as indicative only."""
