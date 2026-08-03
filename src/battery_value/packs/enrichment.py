"""Fill passport gaps from the pack catalogue, recording every substitution.

A scanned passport is frequently incomplete: it names the vehicle but not the
chemistry, or gives energy but not mass. Once the pack model is identified, the
catalogue can supply the rest. What it must never do is quietly overwrite
something the passport actually declared, so every filled field is recorded and
declared values always win.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..passport.models import BatteryPassport
from .models import PackMatch, PackModel


@dataclass(frozen=True, slots=True)
class FilledField:
    """One field the catalogue supplied."""

    field: str
    value: object
    source: str

    def describe(self) -> str:
        """Human-readable provenance line."""
        return f"{self.field} = {self.value} (from {self.source})"


@dataclass(slots=True)
class EnrichmentResult:
    """The outcome of enriching a passport from the catalogue."""

    passport: BatteryPassport
    match: PackMatch | None = None
    filled: list[FilledField] = field(default_factory=list)

    @property
    def pack_model(self) -> PackModel | None:
        """The matched pack model, if any."""
        return self.match.model if self.match else None

    @property
    def was_enriched(self) -> bool:
        """Whether anything was actually filled in."""
        return bool(self.filled)

    def provenance_lines(self) -> list[str]:
        """Audit trail of every substitution."""
        lines: list[str] = []
        if self.match:
            lines.append(f"matched pack model: {self.match.describe()}")
        lines.extend(entry.describe() for entry in self.filled)
        return lines


def enrich_passport(
    passport: BatteryPassport, match: PackMatch | None
) -> EnrichmentResult:
    """Fill missing passport fields from a matched pack model.

    Args:
        passport: The scanned passport. Modified in place and also returned.
        match: The catalogue match, or ``None`` to leave the passport untouched.

    Returns:
        The passport plus a record of which fields the catalogue supplied.
    """
    result = EnrichmentResult(passport=passport, match=match)
    if match is None or not match.is_confident:
        return result

    model = match.model
    source = f"catalogue:{model.key}"
    technical = passport.technical
    identity = passport.identity

    def fill(holder: object, attribute: str, value: object, label: str) -> None:
        if value is None:
            return
        if getattr(holder, attribute, None) in (None, "", 0):
            setattr(holder, attribute, value)
            result.filled.append(FilledField(label, value, source))

    fill(technical, "chemistry_raw", model.chemistry, "technical.chemistry")
    fill(technical, "rated_capacity_kwh", model.rated_kwh, "technical.rated_capacity_kwh")
    fill(technical, "pack_mass_kg", model.pack_mass_kg, "technical.pack_mass_kg")
    fill(technical, "module_count", model.module_count, "technical.module_count")
    fill(technical, "cell_count", model.cell_count, "technical.cell_count")
    fill(
        technical,
        "nominal_voltage_v",
        model.nominal_voltage_v,
        "technical.nominal_voltage_v",
    )
    fill(technical, "cell_format", model.cell_format, "technical.cell_format")
    fill(identity, "manufacturer", model.manufacturer, "identity.manufacturer")
    fill(identity, "model_name", model.label, "identity.model_name")

    # Re-derive health figures that depend on nameplate energy, in case the
    # catalogue is what supplied that energy in the first place.
    rated = technical.rated_kwh
    health = passport.health
    if rated:
        if health.remaining_capacity_kwh is None and health.soh_fraction is not None:
            health.remaining_capacity_kwh = rated * health.soh_fraction
            result.filled.append(
                FilledField(
                    "health.remaining_capacity_kwh",
                    round(health.remaining_capacity_kwh, 2),
                    "derived from catalogue energy x SoH",
                )
            )
        elif health.state_of_health_pct is None and health.remaining_capacity_kwh:
            health.state_of_health_pct = min(
                100.0, health.remaining_capacity_kwh / rated * 100.0
            )
            result.filled.append(
                FilledField(
                    "health.state_of_health_pct",
                    round(health.state_of_health_pct, 1),
                    "derived from measured capacity / catalogue energy",
                )
            )

    return result
