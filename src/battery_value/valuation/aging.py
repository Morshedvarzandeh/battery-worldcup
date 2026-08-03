"""Is this battery ageing normally, and how long has it got?

The passport says 87% health. On its own that is a number without a yardstick.
Held against the pack's own model it becomes the two things an owner can act
on: whether their battery is doing better or worse than others like it, and
roughly when it will fall below the level that buyers care about.

Two guards keep this honest.

**No circular verdicts.** When state of health was itself estimated from age,
comparing it to an age-based curve is comparing the curve to itself. It would
always come back "exactly typical", which reads like a finding and is not one.
So a comparison is only offered when there is real evidence about this
individual pack -- a measurement, or a cycle count.

**A cohort is not a pack.** The curve describes a population. Real examples of
one model scatter by several points at the same age, so the verdict is stated
against that spread rather than against the mean, and a pack inside the spread
is reported as normal rather than as a small anomaly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..materials.degradation import (
    DEFAULT_CLIMATE,
    DegradationLibrary,
    DegradationProfile,
)
from .config import ValuationConfig
from .health import HealthAssessment, HealthSource

#: How far the forecast will look ahead before giving up, in years.
_FORECAST_HORIZON_YEARS = 20.0

#: Years of trajectory to hand to a chart.
_TRAJECTORY_YEARS = 10

#: A pack has to be this old before a comparison means anything. Below it,
#: measurement noise is larger than the difference being measured.
_MINIMUM_COMPARABLE_AGE = 1.0

#: Standard deviations from the cohort mean before a pack is called unusual.
_OUTLIER_SIGMA = 1.0

#: Bounds on the fade ratio. A pack cannot credibly be ageing at a fifth of the
#: cohort rate or four times it; past these, something is wrong with an input
#: rather than with the battery, and an unbounded ratio would produce a
#: confident forecast built on a typo.
_FADE_RATIO_BOUNDS = (0.35, 3.0)


class AgingVerdict(str, Enum):
    """How this pack compares with others of its model at the same age."""

    AHEAD = "ahead"
    """Holding up better than most."""

    TYPICAL = "typical"
    """Within the normal spread for its model and age."""

    BEHIND = "behind"
    """Losing capacity faster than most."""

    UNKNOWN = "unknown"
    """Not enough independent evidence to say."""

    @property
    def label(self) -> str:
        """Short label for tables and chips."""
        return {
            AgingVerdict.AHEAD: "Ageing well",
            AgingVerdict.TYPICAL: "Ageing normally",
            AgingVerdict.BEHIND: "Ageing faster than most",
            AgingVerdict.UNKNOWN: "Not enough information",
        }[self]

    @property
    def tone(self) -> str:
        """``good``, ``fair`` or ``weak`` -- for styling only."""
        return {
            AgingVerdict.AHEAD: "good",
            AgingVerdict.TYPICAL: "good",
            AgingVerdict.BEHIND: "weak",
            AgingVerdict.UNKNOWN: "fair",
        }[self]


@dataclass(frozen=True, slots=True)
class TrajectoryPoint:
    """One year on the health forecast."""

    age_years: float
    projected_soh: float
    cohort_soh: float


@dataclass(slots=True)
class AgingAssessment:
    """What the pack model says about how this battery has aged, and what is left.

    Attributes:
        profile_key: Which profile was used.
        profile_label: What that profile describes, in words.
        is_model_specific: False when a chemistry fallback had to be used.
        expected_soh: What a typical pack of this model would show by now.
        observed_soh: What this pack actually shows.
        deviation_points: Observed minus expected, in health points.
        spread_points: One standard deviation across the cohort at this age.
        verdict: The comparison, or UNKNOWN when one cannot honestly be made.
        fade_ratio: This pack's fade divided by the cohort's. 1.4 means it is
            losing capacity about 40% faster than others of its model.
        annual_fade_ahead: Health points expected to be lost over the next year.
        years_to_resale_floor: Years until it drops below the health a buyer
            wants in a replacement pack. ``None`` if already below, or beyond
            the forecast horizon.
        years_to_storage_floor: The same for home and grid storage, which is
            the last route that pays anything for a working battery.
        cycles_used, cycles_expected: Actual against typical use for the age.
    """

    profile_key: str
    profile_label: str
    is_model_specific: bool
    age_years: float | None
    observed_soh: float
    expected_soh: float | None = None
    deviation_points: float | None = None
    spread_points: float | None = None
    verdict: AgingVerdict = AgingVerdict.UNKNOWN
    fade_ratio: float | None = None
    annual_fade_ahead: float | None = None
    years_to_resale_floor: float | None = None
    years_to_storage_floor: float | None = None
    already_below_resale_floor: bool = False
    already_below_storage_floor: bool = False
    resale_floor: float = 0.75
    storage_floor: float = 0.60
    cycles_used: int | None = None
    cycles_expected: float | None = None
    climate: str = DEFAULT_CLIMATE
    thermal_management: str = "unknown"
    confidence: float = 0.0
    trajectory: list[TrajectoryPoint] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_comparable(self) -> bool:
        """Whether a like-for-like comparison was possible at all."""
        return self.verdict is not AgingVerdict.UNKNOWN

    @property
    def uses_more_than_typical(self) -> bool | None:
        """Whether the pack has been worked harder than a typical example."""
        if self.cycles_used is None or not self.cycles_expected:
            return None
        return self.cycles_used > self.cycles_expected * 1.15

    @property
    def value_at_risk(self) -> bool:
        """Whether the resale floor is close enough to bear on the number today.

        A pack a year from dropping out of the resale market is worth less than
        one with six years of headroom, whatever today's health reading says.
        """
        return (
            self.years_to_resale_floor is not None
            and self.years_to_resale_floor <= 2.0
        )


def _forecast_soh(
    profile: DegradationProfile,
    *,
    at_age: float,
    cycles_per_year: float | None,
    rated_kwh: float,
    climate_factor: float,
    fade_ratio: float,
) -> float:
    """Projected health at ``at_age``, scaled by this pack's own fade rate."""
    cycles = cycles_per_year * at_age if cycles_per_year is not None else None
    raw = profile.fade(
        at_age,
        cycles=cycles,
        rated_kwh=rated_kwh,
        climate_factor=climate_factor,
    )
    return profile.soh_from_fade(max(0.0, raw) * fade_ratio)


def _years_until(
    profile: DegradationProfile,
    *,
    floor: float,
    from_age: float,
    cycles_per_year: float | None,
    rated_kwh: float,
    climate_factor: float,
    fade_ratio: float,
) -> float | None:
    """Years from now until health falls to ``floor``, by bisection.

    ``None`` when the pack stays above the floor for the whole horizon, which
    for a slow-ageing LFP pack is a real and useful answer.
    """

    def soh_at(age: float) -> float:
        return _forecast_soh(
            profile,
            at_age=age,
            cycles_per_year=cycles_per_year,
            rated_kwh=rated_kwh,
            climate_factor=climate_factor,
            fade_ratio=fade_ratio,
        )

    horizon = from_age + _FORECAST_HORIZON_YEARS
    if soh_at(horizon) > floor:
        return None

    low, high = from_age, horizon
    for _ in range(40):
        middle = (low + high) / 2
        if soh_at(middle) > floor:
            low = middle
        else:
            high = middle
    return round(max(0.0, high - from_age), 1)


def assess_aging(
    health: HealthAssessment,
    profile: DegradationProfile | None,
    library: DegradationLibrary,
    config: ValuationConfig,
    *,
    climate: str = DEFAULT_CLIMATE,
) -> AgingAssessment | None:
    """Compare a pack against its model's fade curve and forecast it forward.

    Args:
        health: The resolved health assessment.
        profile: Degradation profile for this pack model, or a chemistry
            fallback. ``None`` disables the whole assessment.
        library: The dataset, for climate factors.
        config: Valuation assumptions, for the resale and storage floors.
        climate: Where the pack has spent its life. Only worth supplying when
            it is genuinely known; the default is temperate.

    Returns:
        An assessment, or ``None`` when there is no profile or no age to work
        from, because a fade curve with no elapsed time says nothing.
    """
    if profile is None or not health.age_years or health.age_years <= 0:
        return None

    age = health.age_years
    rated_kwh = health.rated_kwh
    climate_factor = library.climate_factor(climate, profile.climate_sensitivity)

    cycles = float(health.cycle_count) if health.cycle_count else None
    cycles_expected = profile.reference_cycles_per_year(rated_kwh) * age
    cycles_per_year = (cycles / age) if cycles else (
        profile.reference_cycles_per_year(rated_kwh) or None
    )

    expected = profile.expected_soh(
        age,
        cycles=cycles,
        rated_kwh=rated_kwh,
        climate_factor=climate_factor,
    )

    notes: list[str] = []

    # Only a measurement can settle whether this pack is better or worse than
    # its cohort. Health inferred from age or cycles is the curve talking back:
    # comparing it to the curve would return "typical" no matter what the pack
    # is really doing. A cycle count still says something -- how hard it has
    # been worked -- and that is reported separately.
    comparable = (
        health.source is HealthSource.MEASURED and age >= _MINIMUM_COMPARABLE_AGE
    )
    if health.source is HealthSource.CYCLES:
        notes.append(
            "Health was worked out from how many times the battery has been "
            "charged, not measured directly, so we can say how hard it has been "
            "used but not whether it has held up better or worse than others."
        )
    elif health.source is HealthSource.AGE:
        notes.append(
            "Health was estimated from the battery's age, so we cannot tell "
            "whether this particular pack is doing better or worse than others "
            "like it. A capacity reading would settle that."
        )
    elif health.source is HealthSource.ASSUMED:
        notes.append(
            "Nothing was known about this battery's health, so the figures "
            "below describe a typical pack of this model rather than yours."
        )
    elif age < _MINIMUM_COMPARABLE_AGE:
        notes.append(
            "The battery is less than a year old, which is too early to tell "
            "normal settling from real wear."
        )

    observed = health.soh
    deviation = spread = None
    verdict = AgingVerdict.UNKNOWN
    if comparable:
        spread = profile.spread_at(age) / 100.0
        deviation = observed - expected
        if spread > 0:
            sigma = deviation / spread
            if sigma >= _OUTLIER_SIGMA:
                verdict = AgingVerdict.AHEAD
            elif sigma <= -_OUTLIER_SIGMA:
                verdict = AgingVerdict.BEHIND
            else:
                verdict = AgingVerdict.TYPICAL

    # How fast this pack fades relative to the cohort. Anchoring the forecast to
    # it means a pack already ageing badly is not forecast to suddenly behave.
    expected_fade = 1.0 - expected
    fade_ratio = 1.0
    if comparable and expected_fade > 0.01:
        raw_ratio = (1.0 - observed) / expected_fade
        fade_ratio = min(max(raw_ratio, _FADE_RATIO_BOUNDS[0]), _FADE_RATIO_BOUNDS[1])
        if raw_ratio > _FADE_RATIO_BOUNDS[1]:
            notes.append(
                "This battery has lost far more capacity than any pack of its "
                "type normally does. Have the reading confirmed before acting "
                "on it -- a faulty module or a mis-set meter looks the same "
                "from here."
            )

    forecast_kwargs = {
        "cycles_per_year": cycles_per_year,
        "rated_kwh": rated_kwh,
        "climate_factor": climate_factor,
        "fade_ratio": fade_ratio,
    }

    resale_floor = config.reuse.minimum_soh
    storage_floor = config.second_life.minimum_soh

    years_to_resale = (
        None
        if observed <= resale_floor
        else _years_until(profile, floor=resale_floor, from_age=age, **forecast_kwargs)
    )
    years_to_storage = (
        None
        if observed <= storage_floor
        else _years_until(profile, floor=storage_floor, from_age=age, **forecast_kwargs)
    )

    next_year = _forecast_soh(profile, at_age=age + 1.0, **forecast_kwargs)
    annual_fade = max(0.0, observed - next_year) if comparable else max(
        0.0, expected - next_year
    )

    trajectory = [
        TrajectoryPoint(
            age_years=round(age + step, 1),
            projected_soh=round(
                _forecast_soh(profile, at_age=age + step, **forecast_kwargs), 4
            ),
            cohort_soh=round(
                _forecast_soh(
                    profile,
                    at_age=age + step,
                    cycles_per_year=cycles_per_year,
                    rated_kwh=rated_kwh,
                    climate_factor=climate_factor,
                    fade_ratio=1.0,
                ),
                4,
            ),
        )
        for step in range(_TRAJECTORY_YEARS + 1)
    ]

    if profile.is_fallback:
        notes.append(
            "We do not have wear data for this exact pack, so this is based on "
            "how batteries of the same type generally age."
        )
    if profile.notes:
        notes.append(profile.notes)

    confidence = profile.confidence_factor * health.confidence
    if not comparable:
        confidence *= 0.6

    return AgingAssessment(
        profile_key=profile.key,
        profile_label=profile.label,
        is_model_specific=not profile.is_fallback,
        age_years=round(age, 2),
        observed_soh=round(observed, 4),
        expected_soh=round(expected, 4),
        deviation_points=(
            round(deviation * 100.0, 1) if deviation is not None else None
        ),
        spread_points=(round(spread * 100.0, 1) if spread is not None else None),
        verdict=verdict,
        fade_ratio=round(fade_ratio, 2) if comparable else None,
        annual_fade_ahead=round(annual_fade * 100.0, 2),
        years_to_resale_floor=years_to_resale,
        years_to_storage_floor=years_to_storage,
        already_below_resale_floor=observed <= resale_floor,
        already_below_storage_floor=observed <= storage_floor,
        resale_floor=resale_floor,
        storage_floor=storage_floor,
        cycles_used=health.cycle_count,
        cycles_expected=round(cycles_expected, 0) if cycles_expected else None,
        climate=climate,
        thermal_management=profile.thermal_management,
        confidence=round(min(confidence, 1.0), 3),
        trajectory=trajectory,
        notes=notes,
    )


__all__ = [
    "AgingAssessment",
    "AgingVerdict",
    "TrajectoryPoint",
    "assess_aging",
]
