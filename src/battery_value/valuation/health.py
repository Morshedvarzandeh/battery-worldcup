"""Work out how much life the pack has left.

State of health is the single biggest driver of residual value, and it is also
the field most often missing or stale. This module resolves it from whatever
the passport does provide -- a measurement, a cycle count, or just an age --
and is explicit about which of those it used, because the answer's confidence
depends heavily on that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from ..materials.chemistry import ChemistrySpec
from ..materials.degradation import DegradationProfile
from ..passport.models import BatteryPassport, PackCondition
from .config import ValuationConfig


class HealthSource(str, Enum):
    """Where the state-of-health figure came from."""

    MEASURED = "measured"
    """Declared in the passport, or derived from a measured capacity."""

    CYCLES = "cycles"
    """Estimated from cycle count against the chemistry's rated life."""

    AGE = "age"
    """Estimated from calendar age alone."""

    ASSUMED = "assumed"
    """Nothing was known; the configured default was used."""

    @property
    def confidence(self) -> float:
        """How much to trust a figure from this source."""
        return {
            HealthSource.MEASURED: 1.00,
            HealthSource.CYCLES: 0.72,
            HealthSource.AGE: 0.52,
            HealthSource.ASSUMED: 0.25,
        }[self]


@dataclass(slots=True)
class HealthAssessment:
    """Resolved health, remaining life and anything that limits the pathways."""

    soh: float
    source: HealthSource
    rated_kwh: float
    remaining_kwh: float
    condition: PackCondition
    age_years: float | None = None
    cycle_count: int | None = None
    cycle_life: int | None = None
    remaining_cycles: float | None = None
    remaining_throughput_kwh: float | None = None
    degradation_pct_per_year: float | None = None
    measurement_age_days: int | None = None
    concerns: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """Confidence in the health figure, penalised for a stale measurement."""
        base = self.source.confidence
        if self.measurement_age_days and self.measurement_age_days > 180:
            # A year-old SoH reading on a pack still in service is a guess.
            base *= max(0.6, 1.0 - (self.measurement_age_days - 180) / 1460)
        if self.concerns:
            base *= 0.9
        return round(min(base, 1.0), 3)

    @property
    def is_safe_for_reuse(self) -> bool:
        """Whether anything rules the pack out of a live-reuse pathway."""
        return not self.condition.blocks_reuse and not self.concerns


def _rated_cycle_life(
    chemistry: ChemistrySpec | None,
    profile: DegradationProfile | None,
    declared_life: int | None,
) -> int | None:
    """Cycle life to 80% health, most specific source first.

    The passport's own figure wins, then the pack model's, then the chemistry's.
    A model-specific figure matters: an LFP Model 3 and an NCA Model 3 share a
    body and nothing else about how long their cells last.
    """
    if declared_life:
        return declared_life
    if profile and profile.cycle_life_to_80pct:
        return profile.cycle_life_to_80pct
    return chemistry.typical_cycle_life_to_80pct if chemistry else None


def _estimate_from_cycles(cycle_count: int, cycle_life: int | None) -> float:
    """Estimate SoH from cycles alone.

    Rated cycle life is quoted to 80% SoH, so a pack at its full rated life has
    lost 20% capacity; fade is extrapolated linearly beyond that.
    """
    life = cycle_life or 1800
    return max(0.0, 1.0 - 0.20 * (cycle_count / life))


def assess_health(
    passport: BatteryPassport,
    chemistry: ChemistrySpec | None,
    config: ValuationConfig,
    *,
    as_of: date | None = None,
    profile: DegradationProfile | None = None,
    climate_factor: float = 1.0,
) -> HealthAssessment:
    """Resolve state of health and remaining life for a pack.

    Args:
        passport: The (already enriched) passport.
        chemistry: Resolved chemistry, used for default cycle life.
        config: Valuation assumptions.
        as_of: Valuation date, defaulting to today.
        profile: Degradation profile for the identified pack model. When one is
            available, health estimated from age uses that model's own fade
            curve instead of a single rate applied to every battery ever made.
        climate_factor: Multiplier on calendar fade for the pack's climate.

    Returns:
        A health assessment recording which evidence it relied on.
    """
    today = as_of or date.today()
    rated_kwh = passport.rated_kwh or 0.0
    age_years = passport.age_years(today)
    cycle_count = passport.health.cycle_count
    concerns: list[str] = list(passport.health.safety_flags)

    declared_soh = passport.health.soh_fraction
    declared_life = passport.technical.expected_lifetime_cycles

    cycle_life = _rated_cycle_life(chemistry, profile, declared_life)

    if declared_soh is not None:
        soh, source = declared_soh, HealthSource.MEASURED
    elif cycle_count:
        if profile is not None and age_years:
            # With both a cycle count and an age, the pack model's own curve
            # beats counting cycles alone, which ignores calendar fade entirely
            # and so flatters any pack that has simply been sitting around.
            soh = profile.expected_soh(
                age_years,
                cycles=cycle_count,
                rated_kwh=rated_kwh,
                climate_factor=climate_factor,
            )
        else:
            soh = _estimate_from_cycles(cycle_count, cycle_life)
        source = HealthSource.CYCLES
    elif age_years:
        if profile is not None:
            soh = profile.expected_soh(age_years, climate_factor=climate_factor)
        else:
            soh = max(0.0, 1.0 - age_years * config.calendar_fade_per_year)
        source = HealthSource.AGE
    else:
        soh = config.assumed_soh_when_unknown
        source = HealthSource.ASSUMED
        concerns.append(
            "state of health was not available from any source; "
            f"assumed {soh:.0%}, so this valuation is indicative only"
        )

    soh = min(max(soh, 0.0), 1.0)
    remaining_kwh = passport.remaining_kwh or (rated_kwh * soh)

    # Remaining cycles before the pack drops below the second-life floor.
    remaining_cycles: float | None = None
    remaining_throughput: float | None = None
    if cycle_life:
        headroom = soh - config.second_life.end_of_life_soh
        if headroom > 0:
            # Fade per cycle implied by the rated 20% loss over rated life.
            fade_per_cycle = 0.20 / cycle_life
            remaining_cycles = headroom / fade_per_cycle
            if cycle_count:
                remaining_cycles = max(0.0, min(remaining_cycles, cycle_life * 2.5 - cycle_count))
            remaining_throughput = (
                remaining_cycles
                * rated_kwh
                * ((soh + config.second_life.end_of_life_soh) / 2)
                * config.second_life.usable_dod_window
            )
        else:
            remaining_cycles = 0.0
            remaining_throughput = 0.0

    degradation_rate: float | None = None
    if age_years and age_years > 0.5:
        degradation_rate = (1.0 - soh) / age_years * 100.0

    measurement_age: int | None = None
    if passport.health.measured_at:
        measurement_age = max((today - passport.health.measured_at).days, 0)

    if passport.health.cell_imbalance_mv and passport.health.cell_imbalance_mv > 150:
        concerns.append(
            f"cell imbalance of {passport.health.cell_imbalance_mv:.0f} mV suggests "
            "a weak module; grading before resale is essential"
        )
    if passport.health.over_temperature_events:
        concerns.append(
            f"{passport.health.over_temperature_events} over-temperature event(s) recorded"
        )

    return HealthAssessment(
        soh=soh,
        source=source,
        rated_kwh=rated_kwh,
        remaining_kwh=remaining_kwh,
        condition=passport.health.condition,
        age_years=age_years,
        cycle_count=cycle_count,
        cycle_life=cycle_life,
        remaining_cycles=remaining_cycles,
        remaining_throughput_kwh=remaining_throughput,
        degradation_pct_per_year=degradation_rate,
        measurement_age_days=measurement_age,
        concerns=concerns,
    )
