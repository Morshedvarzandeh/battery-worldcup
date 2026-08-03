"""The normalised battery passport.

Real passports arrive in incompatible shapes -- an EU digital product passport,
a Global Battery Alliance document, an OEM's own telemetry export. Everything
upstream maps into this one model, so the valuation engine only ever sees a
single, well-defined structure, and every field it needs has exactly one home.

Field names follow EU Regulation 2023/1542 Annex XIII where an equivalent
exists.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..materials.chemistry import ChemistrySpec, try_resolve_chemistry

# Days per year used for age arithmetic; calendar-exact ages are not meaningful
# at the precision a residual valuation works to.
_DAYS_PER_YEAR = 365.25


class BatteryCategory(str, Enum):
    """Battery categories as defined by EU Regulation 2023/1542 Article 3."""

    EV = "ev"
    """Electric vehicle traction battery."""

    LMT = "lmt"
    """Light means of transport: e-bikes, scooters."""

    INDUSTRIAL = "industrial"
    """Industrial and stationary storage."""

    SLI = "sli"
    """Starting, lighting and ignition."""

    PORTABLE = "portable"
    """Portable / consumer."""

    UNKNOWN = "unknown"


class PackCondition(str, Enum):
    """Physical condition, which drives dangerous-goods freight cost."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DEFECTIVE = "defective"
    DAMAGED = "damaged"
    THERMAL_EVENT = "thermal_event"

    @property
    def blocks_reuse(self) -> bool:
        """Conditions that rule out resale or repurposing outright."""
        return self in {
            PackCondition.DEFECTIVE,
            PackCondition.DAMAGED,
            PackCondition.THERMAL_EVENT,
        }


class BatteryIdentity(BaseModel):
    """Who made the pack, and which pack it is."""

    model_config = ConfigDict(extra="allow")

    passport_id: str | None = None
    battery_id: str | None = None
    serial_number: str | None = None
    gtin: str | None = None
    manufacturer: str | None = None
    manufacturer_id: str | None = None
    brand: str | None = None
    model_name: str | None = None
    vehicle_model: str | None = None
    manufacturing_date: date | None = None
    manufacturing_country: str | None = None
    placed_on_market_date: date | None = None
    category: BatteryCategory = BatteryCategory.UNKNOWN

    @property
    def display_name(self) -> str:
        """Best available human label for the pack."""
        parts = [p for p in (self.manufacturer or self.brand, self.model_name) if p]
        return " ".join(parts) if parts else (self.battery_id or "Unidentified battery")

    def age_years(self, as_of: date | None = None) -> float | None:
        """Years since manufacture, or since market placement as a fallback."""
        reference = self.manufacturing_date or self.placed_on_market_date
        if reference is None:
            return None
        return max(((as_of or date.today()) - reference).days / _DAYS_PER_YEAR, 0.0)


class BatteryTechnical(BaseModel):
    """Nameplate specification."""

    model_config = ConfigDict(extra="allow")

    chemistry_raw: str | None = None
    rated_capacity_kwh: float | None = Field(default=None, gt=0)
    rated_capacity_ah: float | None = Field(default=None, gt=0)
    nominal_voltage_v: float | None = Field(default=None, gt=0)
    pack_mass_kg: float | None = Field(default=None, gt=0)
    module_count: int | None = Field(default=None, gt=0)
    cell_count: int | None = Field(default=None, gt=0)
    cell_format: str | None = None
    warranty_years: float | None = Field(default=None, ge=0)
    expected_lifetime_cycles: int | None = Field(default=None, gt=0)

    @property
    def rated_kwh(self) -> float | None:
        """Nameplate energy, derived from Ah x V when not stated directly."""
        if self.rated_capacity_kwh:
            return self.rated_capacity_kwh
        if self.rated_capacity_ah and self.nominal_voltage_v:
            return (self.rated_capacity_ah * self.nominal_voltage_v) / 1000.0
        return None

    @property
    def chemistry(self) -> ChemistrySpec | None:
        """Resolved chemistry, or ``None`` when unidentifiable."""
        return try_resolve_chemistry(self.chemistry_raw)


class BatteryHealth(BaseModel):
    """The dynamic section: how much life the pack has left."""

    model_config = ConfigDict(extra="allow")

    state_of_health_pct: float | None = Field(default=None, ge=0, le=100)
    remaining_capacity_kwh: float | None = Field(default=None, ge=0)
    cycle_count: int | None = Field(default=None, ge=0)
    capacity_throughput_kwh: float | None = Field(default=None, ge=0)
    internal_resistance_mohm: float | None = Field(default=None, ge=0)
    round_trip_efficiency_pct: float | None = Field(default=None, ge=0, le=100)
    self_discharge_pct_per_month: float | None = Field(default=None, ge=0)
    cell_imbalance_mv: float | None = Field(default=None, ge=0)
    deep_discharge_events: int | None = Field(default=None, ge=0)
    over_temperature_events: int | None = Field(default=None, ge=0)
    measured_at: date | None = None
    condition: PackCondition = PackCondition.HEALTHY
    safety_flags: list[str] = Field(default_factory=list)

    @field_validator("state_of_health_pct", mode="before")
    @classmethod
    def _accept_fraction(cls, value: Any) -> Any:
        """Accept SoH as either 0-1 or 0-100.

        Exports disagree on this constantly, and reading 0.87 as "0.87% healthy"
        would silently write off a good pack.
        """
        if value is None:
            return value
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return value
        return numeric * 100.0 if 0.0 < numeric <= 1.0 else numeric

    @property
    def soh_fraction(self) -> float | None:
        """State of health as a 0-1 fraction."""
        if self.state_of_health_pct is None:
            return None
        return self.state_of_health_pct / 100.0

    @property
    def has_safety_concern(self) -> bool:
        """Whether anything rules the pack out of a reuse pathway."""
        return bool(self.safety_flags) or self.condition.blocks_reuse


class BatteryComposition(BaseModel):
    """Declared material content and recycled-content shares."""

    model_config = ConfigDict(extra="allow")

    declared_masses_kg: dict[str, float] = Field(default_factory=dict)
    """Element symbol -> contained kg, as declared."""

    declared_mass_fractions: dict[str, float] = Field(default_factory=dict)
    """Element symbol -> share of pack mass, 0-1. Converted to kg when mass is known."""

    recycled_content_pct: dict[str, float] = Field(default_factory=dict)
    hazardous_substances: list[str] = Field(default_factory=list)

    @field_validator("declared_mass_fractions", mode="before")
    @classmethod
    def _normalise_fractions(cls, value: Any) -> Any:
        """Accept shares given as percentages as well as fractions."""
        if not isinstance(value, dict):
            return value
        return {
            element: (share / 100.0 if share is not None and share > 1.0 else share)
            for element, share in value.items()
        }

    def resolved_masses_kg(self, pack_mass_kg: float | None) -> dict[str, float]:
        """Declared content as kg, folding in fractions when pack mass is known.

        Explicit masses win over fractions for the same element.
        """
        resolved: dict[str, float] = {}
        if pack_mass_kg:
            for element, share in self.declared_mass_fractions.items():
                if share and share > 0:
                    resolved[element] = share * pack_mass_kg
        resolved.update(
            {
                element: mass
                for element, mass in self.declared_masses_kg.items()
                if mass and mass > 0
            }
        )
        return resolved

    @property
    def is_empty(self) -> bool:
        """True when the passport declares no composition at all."""
        return not self.declared_masses_kg and not self.declared_mass_fractions


class PassportSource(BaseModel):
    """Where this passport came from, for audit."""

    model_config = ConfigDict(extra="allow")

    kind: str = "unknown"
    """One of ``qr``, ``url``, ``file``, ``inline``, ``manual``."""

    reference: str | None = None
    adapter: str | None = None
    retrieved_at: datetime | None = None
    verified: bool = False
    """True only when the document carried a signature this module checked."""


class BatteryPassport(BaseModel):
    """A battery passport, normalised."""

    model_config = ConfigDict(extra="allow")

    identity: BatteryIdentity = Field(default_factory=BatteryIdentity)
    technical: BatteryTechnical = Field(default_factory=BatteryTechnical)
    health: BatteryHealth = Field(default_factory=BatteryHealth)
    composition: BatteryComposition = Field(default_factory=BatteryComposition)
    source: PassportSource = Field(default_factory=PassportSource)
    raw: dict[str, Any] | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _derive_missing_health(self) -> BatteryPassport:
        """Fill in whichever of SoH / remaining capacity can be derived."""
        rated = self.technical.rated_kwh
        health = self.health

        if health.state_of_health_pct is None and health.remaining_capacity_kwh and rated:
            self.health.state_of_health_pct = min(
                100.0, (health.remaining_capacity_kwh / rated) * 100.0
            )
        elif health.remaining_capacity_kwh is None and health.soh_fraction and rated:
            self.health.remaining_capacity_kwh = rated * health.soh_fraction

        return self

    @property
    def rated_kwh(self) -> float | None:
        """Nameplate energy in kWh."""
        return self.technical.rated_kwh

    @property
    def remaining_kwh(self) -> float | None:
        """Usable energy today, from measured capacity or SoH x nameplate."""
        if self.health.remaining_capacity_kwh:
            return self.health.remaining_capacity_kwh
        rated, soh = self.technical.rated_kwh, self.health.soh_fraction
        return rated * soh if rated and soh else None

    def age_years(self, as_of: date | None = None) -> float | None:
        """Age of the pack in years."""
        return self.identity.age_years(as_of)

    def declared_masses(self) -> dict[str, float]:
        """Declared composition as element -> kg."""
        return self.composition.resolved_masses_kg(self.technical.pack_mass_kg)

    def completeness(self) -> PassportCompleteness:
        """Score how much of what the valuation needs is actually present."""
        return PassportCompleteness.evaluate(self)


class PassportCompleteness(BaseModel):
    """Which valuation-critical fields the passport supplies."""

    present: list[str] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    missing_optional: list[str] = Field(default_factory=list)
    score: float = 0.0

    # Weights reflect how much each field moves the final number.
    _REQUIRED: ClassVar[dict[str, float]] = {
        "rated_capacity_kwh": 0.30,
        "chemistry": 0.25,
        "state_of_health": 0.20,
    }
    _OPTIONAL: ClassVar[dict[str, float]] = {
        "pack_mass_kg": 0.10,
        "manufacturing_date": 0.05,
        "declared_composition": 0.05,
        "cycle_count": 0.03,
        "manufacturer": 0.02,
    }

    @classmethod
    def evaluate(cls, passport: BatteryPassport) -> PassportCompleteness:
        """Build a completeness report for ``passport``."""
        checks: dict[str, bool] = {
            "rated_capacity_kwh": passport.technical.rated_kwh is not None,
            "chemistry": passport.technical.chemistry is not None,
            "state_of_health": passport.health.soh_fraction is not None,
            "pack_mass_kg": passport.technical.pack_mass_kg is not None,
            "manufacturing_date": passport.identity.manufacturing_date is not None,
            "declared_composition": not passport.composition.is_empty,
            "cycle_count": passport.health.cycle_count is not None,
            "manufacturer": bool(passport.identity.manufacturer),
        }

        present = [field for field, ok in checks.items() if ok]
        missing_required = [
            field for field in cls._REQUIRED if not checks.get(field, False)
        ]
        missing_optional = [
            field for field in cls._OPTIONAL if not checks.get(field, False)
        ]
        score = sum(
            weight
            for field, weight in {**cls._REQUIRED, **cls._OPTIONAL}.items()
            if checks.get(field, False)
        )

        return cls(
            present=present,
            missing_required=missing_required,
            missing_optional=missing_optional,
            score=round(score, 3),
        )

    @property
    def is_valuable(self) -> bool:
        """Whether enough is known to attempt a valuation at all."""
        return not self.missing_required
