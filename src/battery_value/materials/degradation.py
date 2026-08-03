"""How fast a pack loses capacity, model by model.

A battery passport reports state of health as a single number. That number
answers "how worn is it" but not the question anyone actually has, which is
"is that normal?" Ninety per cent is excellent on a nine-year-old Leaf and
disappointing on a two-year-old Kona, and nothing in the passport says which.

This module holds the curve that makes the difference legible: for a given pack
model, what a typical example of it looks like at a given age and mileage, and
how widely real examples of it scatter around that.

The curve
---------

Fade splits into two mechanisms that behave nothing alike:

**Calendar fade** is the battery ageing whether you drive it or not, as the
passivation layer on the anode thickens. That process is diffusion-limited, so
it goes with the square root of time: a clear drop in the first year, then a
long flattening. Anyone who has watched a new EV lose three per cent quickly
and then almost nothing for years has seen this shape.

**Cycle fade** is wear from use, roughly linear in energy throughput.

The trap is double counting. Published fade figures come from real cars, which
were being driven, so a model's fade already contains a typical amount of
cycling. Adding a full cycle term on top charges the pack twice for the same
kilometres. So :attr:`DegradationProfile.fade_at_8y` is defined at a reference
annual mileage, and the cycle term prices only the *difference* between what a
pack has actually done and what that reference implies.

Below a knee the curve steepens, because lithium plating and lost active
material start to compound. Packs do not glide gently to zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from .chemistry import ChemistrySpec, _load_json

#: Fade at the rated cycle life, by definition: cycle life is quoted to 80% SoH.
_FADE_AT_RATED_LIFE = 0.20

#: Reference age the dataset quotes fade at.
_REFERENCE_YEARS = 8.0

#: The climates a pack can be asked about, coolest first.
CLIMATES = ("cool", "temperate", "warm", "hot")

DEFAULT_CLIMATE = "temperate"


@dataclass(frozen=True, slots=True)
class DegradationProfile:
    """The fade curve for one pack model, or for a chemistry as a fallback.

    Attributes:
        key: Pack-model key, or ``chemistry:LFP`` for a fallback entry.
        label: What this profile describes, for provenance lines.
        fade_at_8y: Capacity lost after eight years at ``reference_km_per_year``
            in a temperate climate, as a fraction.
        cycle_life_to_80pct: Equivalent full cycles to 80% health, used only for
            the deviation from reference mileage.
        reference_km_per_year: Annual distance ``fade_at_8y`` assumes. Zero for
            stationary products, where mileage means nothing.
        km_per_kwh: Consumption used to turn distance into equivalent cycles.
        thermal_management: ``passive``, ``air`` or ``liquid``. The single
            strongest predictor of how a fleet of one model ages.
        climate_sensitivity: How hard climate hits this pack: ``low``,
            ``medium`` or ``high``.
        spread_points_at_8y: One standard deviation, in state-of-health points,
            across real packs of this model at eight years.
        confidence: How well evidenced the profile is.
        basis: What it was calibrated against.
    """

    key: str
    label: str
    fade_at_8y: float
    cycle_life_to_80pct: int
    reference_km_per_year: float
    km_per_kwh: float
    calendar_exponent: float
    knee_onset_soh: float
    knee_acceleration: float
    thermal_management: str = "unknown"
    climate_sensitivity: str = "medium"
    spread_points_at_8y: float = 5.0
    confidence: str = "low"
    basis: str = ""
    notes: str = ""
    source: str = "bundled"
    is_fallback: bool = False

    # -- the curve ---------------------------------------------------------

    def reference_cycles_per_year(self, rated_kwh: float) -> float:
        """Equivalent full cycles a typical example of this model does a year."""
        if self.reference_km_per_year <= 0 or rated_kwh <= 0 or self.km_per_kwh <= 0:
            return 0.0
        return self.reference_km_per_year / (rated_kwh * self.km_per_kwh)

    def calendar_fade(self, age_years: float, climate_factor: float = 1.0) -> float:
        """Capacity lost to time alone, including the reference amount of use."""
        if age_years <= 0:
            return 0.0
        shape = (age_years / _REFERENCE_YEARS) ** self.calendar_exponent
        return self.fade_at_8y * shape * climate_factor

    def cycle_fade(self, age_years: float, cycles: float, rated_kwh: float) -> float:
        """Extra fade from using the pack harder than a typical owner does.

        Negative for a lightly used pack, which is the point: 40,000 km on an
        eight-year-old car is genuinely better news than 160,000 km, and a model
        that cannot say so is not telling the owner anything they did not know.
        """
        if cycles is None or self.cycle_life_to_80pct <= 0:
            return 0.0
        expected = self.reference_cycles_per_year(rated_kwh) * max(age_years, 0.0)
        if expected <= 0:
            # Stationary products, where there is no reference mileage to
            # deviate from, are priced on their absolute cycle count instead.
            return _FADE_AT_RATED_LIFE * cycles / self.cycle_life_to_80pct
        return _FADE_AT_RATED_LIFE * (cycles - expected) / self.cycle_life_to_80pct

    def fade(
        self,
        age_years: float,
        *,
        cycles: float | None = None,
        rated_kwh: float = 0.0,
        climate_factor: float = 1.0,
    ) -> float:
        """Total capacity lost, before the knee is applied.

        Exposed separately so a forecast can scale a pack's own fade rate up or
        down and then take the knee, rather than steepening an already-steepened
        curve.
        """
        total = self.calendar_fade(age_years, climate_factor)
        if cycles is not None:
            total += self.cycle_fade(age_years, cycles, rated_kwh)
        return total

    def soh_from_fade(self, fade: float) -> float:
        """Turn a fade fraction into a state of health, steepening past the knee."""
        soh = 1.0 - fade
        if soh < self.knee_onset_soh:
            excess = self.knee_onset_soh - soh
            soh = self.knee_onset_soh - excess * self.knee_acceleration
        return max(0.0, min(1.0, soh))

    def expected_soh(
        self,
        age_years: float,
        *,
        cycles: float | None = None,
        rated_kwh: float = 0.0,
        climate_factor: float = 1.0,
    ) -> float:
        """State of health a typical pack of this model would have by now."""
        return self.soh_from_fade(
            self.fade(
                age_years,
                cycles=cycles,
                rated_kwh=rated_kwh,
                climate_factor=climate_factor,
            )
        )

    def spread_at(self, age_years: float) -> float:
        """One standard deviation in SoH points at ``age_years``.

        Scattered by the same square root as the mean: brand-new packs of one
        model are near-identical, and they diverge as they age.
        """
        if age_years <= 0:
            return 0.0
        shape = (age_years / _REFERENCE_YEARS) ** self.calendar_exponent
        # A floor keeps a two-year-old pack from being judged against an
        # implausibly tight band and declared an outlier on measurement noise.
        return max(1.5, self.spread_points_at_8y * shape)

    @property
    def confidence_factor(self) -> float:
        """How much to trust this profile, 0-1."""
        return {"high": 0.9, "medium": 0.7, "low": 0.5}.get(self.confidence, 0.5)

    @property
    def cooling_in_plain_words(self) -> str:
        """How the pack keeps its temperature, for someone who is not an engineer."""
        return {
            "liquid": "liquid-cooled",
            "air": "fan-cooled",
            "passive": "not cooled at all",
        }.get(self.thermal_management, "")


@dataclass(frozen=True, slots=True)
class DegradationLibrary:
    """The loaded degradation dataset, with its fallbacks."""

    updated: str
    notes: tuple[str, ...]
    sources: tuple[str, ...]
    climate_factors: dict[str, float]
    climate_sensitivity_weights: dict[str, float]
    by_pack_model: dict[str, DegradationProfile] = field(default_factory=dict)
    by_chemistry: dict[str, DegradationProfile] = field(default_factory=dict)
    source: str = "bundled"

    def climate_factor(self, climate: str, sensitivity: str) -> float:
        """Multiplier on calendar fade for a climate, weighted by sensitivity.

        A liquid-cooled pack in Seville is not in the same trouble a Leaf is,
        so the climate multiplier is scaled by how exposed the model actually
        is rather than applied flat.
        """
        base = self.climate_factors.get(climate, 1.0)
        weight = self.climate_sensitivity_weights.get(sensitivity, 1.0)
        return max(0.5, 1.0 + (base - 1.0) * weight)

    def for_pack_model(self, key: str | None) -> DegradationProfile | None:
        """The profile for a known pack model, if there is one."""
        return self.by_pack_model.get(key) if key else None

    def for_chemistry(self, chemistry: ChemistrySpec | str | None) -> DegradationProfile | None:
        """The fallback profile for a chemistry."""
        if chemistry is None:
            return None
        key = chemistry if isinstance(chemistry, str) else chemistry.key
        return self.by_chemistry.get(key.strip().upper())

    def resolve(
        self,
        pack_model_key: str | None,
        chemistry: ChemistrySpec | str | None,
    ) -> DegradationProfile | None:
        """Best available profile: the exact model first, then its chemistry."""
        return self.for_pack_model(pack_model_key) or self.for_chemistry(chemistry)


def _profile_from_entry(
    entry: dict[str, Any],
    defaults: dict[str, Any],
    *,
    key: str,
    label: str,
    is_fallback: bool,
    source: str = "bundled",
) -> DegradationProfile:
    """Build one profile, filling anything the entry omits from the defaults."""

    def value(name: str, fallback: Any = None) -> Any:
        got = entry.get(name)
        return got if got is not None else defaults.get(name, fallback)

    return DegradationProfile(
        key=key,
        label=label,
        fade_at_8y=float(entry["fade_at_8y"]),
        cycle_life_to_80pct=int(value("cycle_life_to_80pct", 0) or 0),
        reference_km_per_year=float(value("reference_km_per_year", 0.0)),
        km_per_kwh=float(value("km_per_kwh", 5.5)),
        calendar_exponent=float(value("calendar_exponent", 0.5)),
        knee_onset_soh=float(value("knee_onset_soh", 0.70)),
        knee_acceleration=float(value("knee_acceleration", 1.6)),
        thermal_management=entry.get("thermal_management", "unknown"),
        climate_sensitivity=str(value("climate_sensitivity", "medium")),
        spread_points_at_8y=float(value("spread_points_at_8y", 5.0)),
        confidence=str(value("confidence", "low")),
        basis=entry.get("basis", ""),
        notes=entry.get("notes", ""),
        source=source,
        is_fallback=is_fallback,
    )


def build_library(
    raw: dict[str, Any],
    *,
    pack_labels: dict[str, str] | None = None,
    source: str = "bundled",
) -> DegradationLibrary:
    """Assemble a library from the dataset's raw shape.

    Args:
        raw: Parsed dataset.
        pack_labels: Optional pack-model key to human label, so provenance can
            say "Nissan Leaf ZE1 40 kWh" rather than ``nissan-leaf-ze1-40``.
        source: Where these profiles came from, for provenance.
    """
    defaults = dict(raw.get("defaults", {}))
    labels = pack_labels or {}

    by_pack_model: dict[str, DegradationProfile] = {}
    for entry in raw.get("profiles", []):
        key = entry["pack_model"]
        by_pack_model[key] = _profile_from_entry(
            entry,
            defaults,
            key=key,
            label=labels.get(key, key),
            is_fallback=False,
            source=source,
        )

    by_chemistry: dict[str, DegradationProfile] = {}
    for entry in raw.get("fallback_by_chemistry", []):
        key = str(entry["chemistry"]).strip().upper()
        by_chemistry[key] = _profile_from_entry(
            entry,
            defaults,
            key=f"chemistry:{key}",
            label=f"typical {key} pack",
            is_fallback=True,
            source=source,
        )

    return DegradationLibrary(
        updated=str(raw.get("updated", "")),
        notes=tuple(raw.get("notes", ())),
        sources=tuple(raw.get("sources", ())),
        climate_factors=dict(raw.get("climate_factors", {})),
        climate_sensitivity_weights=dict(raw.get("climate_sensitivity_weights", {})),
        by_pack_model=by_pack_model,
        by_chemistry=by_chemistry,
        source=source,
    )


@lru_cache(maxsize=1)
def load_degradation() -> DegradationLibrary:
    """Load and cache the bundled degradation dataset."""
    return build_library(_load_json("degradation.json"), pack_labels=_pack_labels())


def _pack_labels() -> dict[str, str]:
    """Pack-model keys to their human labels, for readable provenance."""
    try:
        from ..packs.catalogue import load_catalogue

        return {model.key: model.label for model in load_catalogue().models}
    except Exception:  # noqa: BLE001 - labels are cosmetic, never fail the load
        return {}


__all__ = [
    "CLIMATES",
    "DEFAULT_CLIMATE",
    "DegradationLibrary",
    "DegradationProfile",
    "build_library",
    "load_degradation",
]
