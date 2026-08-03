"""Mass, energy and time unit handling.

Everything inside the valuation engine is normalised to **kg** for mass and
**kWh** for energy. Conversions happen once, at the edges, so no downstream
formula ever has to ask which unit it is holding.
"""

from __future__ import annotations

from enum import Enum

from .errors import UnitError


class MassUnit(str, Enum):
    """Mass units seen in battery passports and metal price quotes."""

    GRAM = "g"
    KILOGRAM = "kg"
    TONNE = "t"
    POUND = "lb"
    TROY_OUNCE = "ozt"
    SHORT_TON = "ton_us"


class EnergyUnit(str, Enum):
    """Energy units seen in battery nameplate data."""

    WATT_HOUR = "Wh"
    KILOWATT_HOUR = "kWh"
    MEGAWATT_HOUR = "MWh"


# Metric tonne is the metals-market default ("USD/t" always means metric).
_MASS_TO_KG: dict[MassUnit, float] = {
    MassUnit.GRAM: 1e-3,
    MassUnit.KILOGRAM: 1.0,
    MassUnit.TONNE: 1000.0,
    MassUnit.POUND: 0.45359237,
    MassUnit.TROY_OUNCE: 0.0311034768,
    MassUnit.SHORT_TON: 907.18474,
}

_ENERGY_TO_KWH: dict[EnergyUnit, float] = {
    EnergyUnit.WATT_HOUR: 1e-3,
    EnergyUnit.KILOWATT_HOUR: 1.0,
    EnergyUnit.MEGAWATT_HOUR: 1000.0,
}

# Spellings that turn up in real passport exports and price feeds.
_MASS_ALIASES: dict[str, MassUnit] = {
    "g": MassUnit.GRAM,
    "gram": MassUnit.GRAM,
    "grams": MassUnit.GRAM,
    "kg": MassUnit.KILOGRAM,
    "kgs": MassUnit.KILOGRAM,
    "kilogram": MassUnit.KILOGRAM,
    "kilograms": MassUnit.KILOGRAM,
    "t": MassUnit.TONNE,
    "mt": MassUnit.TONNE,
    "tonne": MassUnit.TONNE,
    "tonnes": MassUnit.TONNE,
    "metric_ton": MassUnit.TONNE,
    "metric ton": MassUnit.TONNE,
    "lb": MassUnit.POUND,
    "lbs": MassUnit.POUND,
    "pound": MassUnit.POUND,
    "pounds": MassUnit.POUND,
    "ozt": MassUnit.TROY_OUNCE,
    "troy_ounce": MassUnit.TROY_OUNCE,
    "toz": MassUnit.TROY_OUNCE,
    "ton_us": MassUnit.SHORT_TON,
    "short_ton": MassUnit.SHORT_TON,
}

_ENERGY_ALIASES: dict[str, EnergyUnit] = {
    "wh": EnergyUnit.WATT_HOUR,
    "watt_hour": EnergyUnit.WATT_HOUR,
    "watthour": EnergyUnit.WATT_HOUR,
    "kwh": EnergyUnit.KILOWATT_HOUR,
    "kw_h": EnergyUnit.KILOWATT_HOUR,
    "kilowatt_hour": EnergyUnit.KILOWATT_HOUR,
    "mwh": EnergyUnit.MEGAWATT_HOUR,
    "megawatt_hour": EnergyUnit.MEGAWATT_HOUR,
}


def parse_mass_unit(raw: str | MassUnit) -> MassUnit:
    """Resolve a free-text mass unit, case- and separator-insensitively."""
    if isinstance(raw, MassUnit):
        return raw
    key = str(raw).strip().lower().replace("-", "_")
    try:
        return _MASS_ALIASES[key]
    except KeyError:
        raise UnitError(f"unrecognised mass unit: {raw!r}") from None


def parse_energy_unit(raw: str | EnergyUnit) -> EnergyUnit:
    """Resolve a free-text energy unit, case- and separator-insensitively."""
    if isinstance(raw, EnergyUnit):
        return raw
    key = str(raw).strip().lower().replace("-", "_")
    try:
        return _ENERGY_ALIASES[key]
    except KeyError:
        raise UnitError(f"unrecognised energy unit: {raw!r}") from None


def to_kg(value: float, unit: str | MassUnit) -> float:
    """Convert a mass to kilograms."""
    return value * _MASS_TO_KG[parse_mass_unit(unit)]


def from_kg(value_kg: float, unit: str | MassUnit) -> float:
    """Convert kilograms to another mass unit."""
    return value_kg / _MASS_TO_KG[parse_mass_unit(unit)]


def convert_mass(value: float, source: str | MassUnit, target: str | MassUnit) -> float:
    """Convert a mass between any two supported units."""
    return from_kg(to_kg(value, source), target)


def to_kwh(value: float, unit: str | EnergyUnit) -> float:
    """Convert an energy to kilowatt-hours."""
    return value * _ENERGY_TO_KWH[parse_energy_unit(unit)]


def from_kwh(value_kwh: float, unit: str | EnergyUnit) -> float:
    """Convert kilowatt-hours to another energy unit."""
    return value_kwh / _ENERGY_TO_KWH[parse_energy_unit(unit)]


def convert_energy(
    value: float, source: str | EnergyUnit, target: str | EnergyUnit
) -> float:
    """Convert an energy between any two supported units."""
    return from_kwh(to_kwh(value, source), target)
