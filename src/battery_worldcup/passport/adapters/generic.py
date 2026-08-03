"""A schema-agnostic adapter that finds known fields anywhere in a document.

Most passports we will be handed are neither strict EU DPP nor strict GBA JSON:
they are an OEM's own export with the right information under idiosyncratic
names, nested somewhere unhelpful. Rather than fail, this adapter flattens the
whole document and matches every leaf against an alias table.

It is the fallback for unrecognised schemas, and it also supplies the field
lookup that the schema-specific adapters build on.
"""

from __future__ import annotations

from typing import Any, Iterable

from ...units import parse_energy_unit, parse_mass_unit, to_kg, to_kwh
from ..models import (
    BatteryComposition,
    BatteryHealth,
    BatteryIdentity,
    BatteryPassport,
    BatteryTechnical,
    PackCondition,
)
from .base import (
    PassportAdapter,
    normalise_key,
    to_date,
    to_float,
    to_int,
    to_str,
    unwrap_value,
)

# Element symbols keyed by every name a passport might use for them.
ELEMENT_ALIASES: dict[str, str] = {
    "cobalt": "Co",
    "co": "Co",
    "lithium": "Li",
    "li": "Li",
    "nickel": "Ni",
    "ni": "Ni",
    "lead": "Pb",
    "pb": "Pb",
    "copper": "Cu",
    "cu": "Cu",
    "aluminium": "Al",
    "aluminum": "Al",
    "al": "Al",
    "manganese": "Mn",
    "mn": "Mn",
    "iron": "Fe",
    "fe": "Fe",
    "steel": "Fe",
    "graphite": "C",
    "naturalgraphite": "C",
    "carbon": "C",
    "titanium": "Ti",
    "ti": "Ti",
    "phosphorus": "P",
    "sodium": "Na",
}

# Ordered alias lists. Earlier entries win, so specific names beat vague ones.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "passport_id": ("passportid", "batterypassportid", "dppid", "productpassportid"),
    "battery_id": ("batteryid", "batteryidentifier", "packid", "uniqueidentifier", "id"),
    "serial_number": ("serialnumber", "serial", "serialno", "packserialnumber"),
    "gtin": ("gtin", "ean", "globaltradeitemnumber"),
    "manufacturer": (
        "manufacturername",
        "manufacturer",
        "producername",
        "producer",
        "oem",
        "companyname",
    ),
    "manufacturer_id": ("manufactureridentifier", "manufacturerid", "operatorid"),
    "brand": ("brand", "brandname", "make"),
    "model_name": ("batterymodel", "modelname", "model", "producttype", "type"),
    "vehicle_model": ("vehiclemodel", "vehicletype", "applicationmodel", "carmodel"),
    "manufacturing_date": (
        "manufacturingdate",
        "dateofmanufacture",
        "productiondate",
        "manufacturedate",
        "buildate",
        "builddate",
    ),
    "manufacturing_country": (
        "manufacturingplace",
        "countryofmanufacture",
        "manufacturingcountry",
        "placeofmanufacture",
    ),
    "placed_on_market_date": (
        "placedonmarketdate",
        "dateofplacingonmarket",
        "firstregistrationdate",
        "commissioningdate",
    ),
    "category": ("batterycategory", "category", "batterytype", "applicationcategory"),
    "chemistry_raw": (
        "batterychemistry",
        "cellchemistry",
        "chemistry",
        "cathodechemistry",
        "electrochemistry",
        "chemistrytype",
    ),
    "rated_capacity_kwh": (
        "ratedcapacitykwh",
        "ratedenergykwh",
        "nominalenergykwh",
        "energycapacitykwh",
        "usableenergykwh",
        "ratedenergy",
        "nominalenergy",
        "energycapacity",
        "totalenergy",
        "batterycapacitykwh",
    ),
    "rated_capacity_ah": (
        "ratedcapacityah",
        "nominalcapacityah",
        "ratedcapacity",
        "nominalcapacity",
        "capacityah",
    ),
    "nominal_voltage_v": (
        "nominalvoltage",
        "ratedvoltage",
        "packvoltage",
        "voltage",
        "nominalvoltagev",
    ),
    "pack_mass_kg": (
        "batterymass",
        "packmass",
        "packweight",
        "batteryweight",
        "grossmass",
        "totalmass",
        "weight",
        "mass",
    ),
    "module_count": ("numberofmodules", "modulecount", "modules"),
    "cell_count": ("numberofcells", "cellcount", "cells"),
    "cell_format": ("cellformat", "celltype", "formfactor"),
    "warranty_years": ("warrantyperiod", "warrantyyears", "guaranteeperiod"),
    "expected_lifetime_cycles": (
        "expectedlifetimecycles",
        "ratedcyclelife",
        "cyclelife",
        "expectedcycles",
        "designcycles",
    ),
    "state_of_health_pct": (
        "stateofhealth",
        "soh",
        "sohpercent",
        "healthstate",
        "capacityfade",
        "remainingcapacitypercentage",
        "certifiedstateofhealth",
    ),
    "remaining_capacity_kwh": (
        "remainingcapacitykwh",
        "remainingenergy",
        "actualcapacitykwh",
        "measuredcapacitykwh",
        "currentcapacity",
        "remainingcapacity",
    ),
    "cycle_count": (
        "cyclecount",
        "numberoffullcycles",
        "chargedischargecycles",
        "equivalentfullcycles",
        "cycles",
    ),
    "capacity_throughput_kwh": (
        "energythroughput",
        "capacitythroughput",
        "totalenergythroughput",
        "lifetimethroughput",
    ),
    "internal_resistance_mohm": (
        "internalresistance",
        "ohmicresistance",
        "packresistance",
        "dcinternalresistance",
    ),
    "round_trip_efficiency_pct": (
        "roundtripefficiency",
        "energyefficiency",
        "rte",
        "coulombicefficiency",
    ),
    "self_discharge_pct_per_month": ("selfdischarge", "selfdischargerate"),
    "cell_imbalance_mv": ("cellimbalance", "voltagespread", "celldeviation"),
    "deep_discharge_events": ("deepdischargeevents", "numberofdeepdischarges"),
    "over_temperature_events": (
        "overtemperatureevents",
        "temperatureexcursions",
        "thermalevents",
    ),
    "measured_at": (
        "measurementdate",
        "dateofmeasurement",
        "lastupdated",
        "statusdate",
        "assessmentdate",
    ),
    "condition": ("condition", "packcondition", "batterystatus", "statusofbattery"),
}

# Category strings seen in the wild, mapped onto the regulation's categories.
_CATEGORY_HINTS: dict[str, str] = {
    "ev": "ev",
    "electricvehicle": "ev",
    "evbattery": "ev",
    "traction": "ev",
    "automotive": "ev",
    "lmt": "lmt",
    "lightmeansoftransport": "lmt",
    "industrial": "industrial",
    "stationary": "industrial",
    "ess": "industrial",
    "sli": "sli",
    "startinglightingignition": "sli",
    "portable": "portable",
    "consumer": "portable",
}

_CONDITION_HINTS: dict[str, PackCondition] = {
    "healthy": PackCondition.HEALTHY,
    "ok": PackCondition.HEALTHY,
    "good": PackCondition.HEALTHY,
    "normal": PackCondition.HEALTHY,
    "degraded": PackCondition.DEGRADED,
    "worn": PackCondition.DEGRADED,
    "endoflife": PackCondition.DEGRADED,
    "defective": PackCondition.DEFECTIVE,
    "faulty": PackCondition.DEFECTIVE,
    "fault": PackCondition.DEFECTIVE,
    "damaged": PackCondition.DAMAGED,
    "crashed": PackCondition.DAMAGED,
    "accident": PackCondition.DAMAGED,
    "thermalevent": PackCondition.THERMAL_EVENT,
    "thermalrunaway": PackCondition.THERMAL_EVENT,
    "fire": PackCondition.THERMAL_EVENT,
}

_SAFETY_KEYS = (
    "safetyflags",
    "safetyissues",
    "warnings",
    "faults",
    "faultcodes",
    "activefaults",
    "negativeevents",
    "incidents",
)


class FlatDocument:
    """A flattened view of a nested document, queryable by field alias."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.leaves: list[tuple[str, str, Any]] = []
        """(normalised leaf key, normalised full path, value)"""
        self._collect(document, ())

    def _collect(self, node: Any, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            # A unit-wrapped scalar is a leaf, not a branch to descend into.
            value, _ = unwrap_value(node)
            if value is not None and not isinstance(value, (dict, list)) and path:
                self.leaves.append(
                    (normalise_key(path[-1]), normalise_key("".join(path)), node)
                )
                return
            for key, child in node.items():
                self._collect(child, (*path, str(key)))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                self._collect(child, (*path, str(index)))
        elif path:
            self.leaves.append(
                (normalise_key(path[-1]), normalise_key("".join(path)), node)
            )

    def find(self, aliases: Iterable[str]) -> Any | None:
        """First value whose key matches an alias, best match first.

        Exact leaf-key matches beat path suffixes, which beat substrings, so a
        field literally called ``stateOfHealth`` wins over one merely nested
        under a ``stateOfHealth`` object.
        """
        alias_list = list(aliases)
        for alias in alias_list:
            for leaf_key, _, value in self.leaves:
                if leaf_key == alias:
                    return value
        for alias in alias_list:
            for _, full_path, value in self.leaves:
                if full_path.endswith(alias):
                    return value
        for alias in alias_list:
            if len(alias) < 5:
                continue  # short aliases match far too eagerly as substrings
            for _, full_path, value in self.leaves:
                if alias in full_path:
                    return value
        return None

    def find_all(self, predicate) -> list[tuple[str, str, Any]]:
        """Every leaf satisfying ``predicate(leaf_key, full_path, value)``."""
        return [leaf for leaf in self.leaves if predicate(*leaf)]

    def get(self, field: str) -> Any | None:
        """Look up a normalised field by its alias table entry."""
        return self.find(FIELD_ALIASES.get(field, (field,)))


def _energy_kwh(raw: Any) -> float | None:
    """Read an energy value, honouring a unit if the document supplied one."""
    value, unit = unwrap_value(raw)
    number = to_float(value)
    if number is None:
        return None
    if unit:
        try:
            return to_kwh(number, parse_energy_unit(unit))
        except Exception:  # noqa: BLE001 - unknown unit falls through to heuristics
            pass
    # No unit given: a "capacity" in the hundreds is almost certainly Wh.
    return number / 1000.0 if number > 400 else number


# Keys that could mean either energy (kWh) or charge (Ah). A passport writing
# `"ratedCapacity": {"value": 75, "unit": "kWh"}` must not be read as 75 Ah --
# multiplied by a 400 V pack that would report 30 kWh instead of 75 kWh.
_CAPACITY_KEY_HINTS = (
    "ratedcapacity",
    "nominalcapacity",
    "ratedenergy",
    "nominalenergy",
    "energycapacity",
    "batterycapacity",
    "usableenergy",
    "totalenergy",
    "capacity",
)
_REMAINING_KEY_HINTS = (
    "remainingcapacity",
    "remainingenergy",
    "actualcapacity",
    "measuredcapacity",
    "currentcapacity",
)
_ENERGY_UNITS = {"kwh", "wh", "mwh", "kilowatthour", "watthour", "megawatthour"}
_CHARGE_UNITS = {"ah", "mah", "amperehour", "amperehours", "milliamperehour"}


def _classify_capacity(unit: str | None, leaf_key: str, path: str) -> str | None:
    """Decide whether a capacity leaf is energy, charge, or ambiguous."""
    normalised_unit = normalise_key(unit or "")
    if normalised_unit in _ENERGY_UNITS:
        return "energy"
    if normalised_unit in _CHARGE_UNITS:
        return "charge"

    # No usable unit field: fall back to what the key itself says.
    for hint in (leaf_key, path):
        if hint.endswith(("kwh", "wh", "mwh")) or "energy" in hint:
            return "energy"
        if hint.endswith(("ah", "mah")):
            return "charge"
    return None


def _capacity_value(raw: Any, kind: str) -> float | None:
    """Convert a classified capacity leaf into kWh (energy) or Ah (charge)."""
    value, unit = unwrap_value(raw)
    number = to_float(value)
    if number is None or number <= 0:
        return None
    normalised_unit = normalise_key(unit or "")
    if kind == "energy":
        if normalised_unit in {"wh", "watthour"}:
            return number / 1000.0
        if normalised_unit in {"mwh", "megawatthour"}:
            return number * 1000.0
        return number
    if normalised_unit in {"mah", "milliamperehour"}:
        return number / 1000.0
    return number


def resolve_capacities(flat: FlatDocument) -> tuple[float | None, float | None, float | None]:
    """Return ``(rated_kwh, rated_ah, remaining_kwh)`` read unit-first.

    Scanning every capacity-like leaf and routing it by its declared unit is
    what stops an energy figure being silently treated as a charge figure.
    """
    rated_kwh: float | None = None
    rated_ah: float | None = None
    remaining_kwh: float | None = None
    ambiguous: list[float] = []

    for leaf_key, path, raw in flat.leaves:
        is_remaining = any(hint in path for hint in _REMAINING_KEY_HINTS)
        is_capacity = is_remaining or any(
            hint in leaf_key or hint in path for hint in _CAPACITY_KEY_HINTS
        )
        if not is_capacity:
            continue

        _, unit = unwrap_value(raw)
        kind = _classify_capacity(unit, leaf_key, path)

        if is_remaining:
            if remaining_kwh is None and kind != "charge":
                remaining_kwh = _capacity_value(raw, "energy")
            continue

        if kind == "energy" and rated_kwh is None:
            rated_kwh = _capacity_value(raw, "energy")
        elif kind == "charge" and rated_ah is None:
            rated_ah = _capacity_value(raw, "charge")
        elif kind is None:
            number = to_float(raw)
            if number and number > 0:
                ambiguous.append(number)

    # An unlabelled capacity is energy if we have nothing better; values in the
    # hundreds are Wh rather than kWh.
    if rated_kwh is None and rated_ah is None and ambiguous:
        candidate = ambiguous[0]
        rated_kwh = candidate / 1000.0 if candidate > 400 else candidate

    return rated_kwh, rated_ah, remaining_kwh


def _mass_kg(raw: Any) -> float | None:
    """Read a mass value, honouring a unit if the document supplied one."""
    value, unit = unwrap_value(raw)
    number = to_float(value)
    if number is None:
        return None
    if unit:
        try:
            return to_kg(number, parse_mass_unit(unit))
        except Exception:  # noqa: BLE001 - unknown unit falls through
            pass
    return number


def _category(raw: Any) -> str:
    text = normalise_key(to_str(raw) or "")
    for hint, category in _CATEGORY_HINTS.items():
        if hint and hint in text:
            return category
    return "unknown"


def _condition(raw: Any) -> PackCondition:
    text = normalise_key(to_str(raw) or "")
    for hint, condition in _CONDITION_HINTS.items():
        if hint and hint in text:
            return condition
    return PackCondition.HEALTHY


# Fields naming the substance inside an array-of-records composition list.
_SUBSTANCE_KEYS = (
    "substance",
    "material",
    "materialname",
    "element",
    "name",
    "rawmaterial",
    "criticalrawmaterial",
    "substancename",
)
# Fields carrying the quantity alongside it.
_MASS_KEYS = ("masskg", "mass", "massingkg", "weight", "weightkg", "quantity", "amount")
_FRACTION_KEYS = (
    "massfraction",
    "fraction",
    "share",
    "percentage",
    "percent",
    "content",
    "contentpercent",
    "massshare",
    "concentration",
)


def _records_from(node: Any) -> list[dict[str, Any]]:
    """Every dict inside any list nested anywhere in ``node``."""
    found: list[dict[str, Any]] = []

    def walk(current: Any) -> None:
        if isinstance(current, list):
            for item in current:
                if isinstance(item, dict):
                    found.append(item)
                walk(item)
        elif isinstance(current, dict):
            for value in current.values():
                walk(value)

    walk(node)
    return found


def composition_from_records(
    document: Any,
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Read composition given as a list of records.

    Handles the Annex XIII shape where the substance name sits in a sibling
    field rather than in the key path::

        [{"substance": "Cobalt", "massKg": 6.9}, ...]
    """
    masses: dict[str, float] = {}
    fractions: dict[str, float] = {}
    recycled: dict[str, float] = {}

    for record in _records_from(document):
        normalised = {normalise_key(key): value for key, value in record.items()}

        element: str | None = None
        for key in _SUBSTANCE_KEYS:
            name = to_str(normalised.get(key))
            if name:
                element = ELEMENT_ALIASES.get(normalise_key(name))
                if element:
                    break
        if element is None:
            continue

        is_recycled = any("recycl" in key for key in normalised)

        for key, value in normalised.items():
            number = to_float(value)
            if number is None or number <= 0:
                continue
            _, unit = unwrap_value(value)
            normalised_unit = normalise_key(unit or "")

            if is_recycled and "recycl" in key:
                recycled[element] = number if number > 1 else number * 100.0
            elif key in _MASS_KEYS or normalised_unit in {"kg", "kilogram", "kilograms"}:
                masses[element] = number
            elif normalised_unit in {"g", "gram", "grams"}:
                masses[element] = number / 1000.0
            elif key in _FRACTION_KEYS or normalised_unit in {"percent", "pct", "%"}:
                fractions[element] = number

    return masses, fractions, recycled


def extract_composition(
    flat: FlatDocument, document: Any | None = None
) -> BatteryComposition:
    """Pull declared material content out of whatever shape it was given in.

    Handles both ``{"cobalt": {"value": 6.7, "unit": "kg"}}`` and
    ``[{"substance": "Cobalt", "massFraction": 0.014}]``.
    """
    masses: dict[str, float] = {}
    fractions: dict[str, float] = {}
    recycled: dict[str, float] = {}

    if document is not None:
        masses, fractions, recycled = composition_from_records(document)

    for leaf_key, full_path, raw in flat.leaves:
        element = ELEMENT_ALIASES.get(leaf_key)

        # Array form: the element name sits in a sibling field, so fall back to
        # matching the element name anywhere in the path.
        if element is None:
            for alias, symbol in ELEMENT_ALIASES.items():
                if len(alias) > 2 and alias in full_path:
                    element = symbol
                    break
        if element is None:
            continue

        value, unit = unwrap_value(raw)
        number = to_float(value)
        if number is None or number <= 0:
            continue

        normalised_unit = normalise_key(unit or "")
        is_recycled = "recycled" in full_path or "recyclat" in full_path

        # Record-derived values are more explicit, so they are never overwritten
        # by a looser key-path match for the same element.
        if is_recycled:
            recycled.setdefault(element, number if number > 1 else number * 100.0)
        elif normalised_unit in {"kg", "kilogram", "kilograms"} or "masskg" in full_path:
            masses.setdefault(element, number)
        elif normalised_unit in {"g", "gram", "grams"}:
            masses.setdefault(element, number / 1000.0)
        elif (
            normalised_unit in {"", "percent", "pct", "%"}
            and ("fraction" in full_path or "share" in full_path or "content" in full_path
                 or "percent" in full_path)
        ):
            fractions.setdefault(element, number)
        elif normalised_unit in {"percent", "pct", "%"}:
            fractions.setdefault(element, number)

    return BatteryComposition(
        declared_masses_kg=masses,
        declared_mass_fractions=fractions,
        recycled_content_pct=recycled,
    )


def _safety_flags(flat: FlatDocument) -> list[str]:
    flags: list[str] = []
    for leaf_key, full_path, raw in flat.leaves:
        if not any(key in full_path or key == leaf_key for key in _SAFETY_KEYS):
            continue
        value, _ = unwrap_value(raw)
        if isinstance(value, bool):
            if value:
                flags.append(leaf_key)
        else:
            text = to_str(value)
            if text and normalise_key(text) not in {"none", "no", "false", "ok", "0"}:
                flags.append(text)
    return flags


def build_passport(flat: FlatDocument, document: dict[str, Any]) -> BatteryPassport:
    """Assemble a passport from a flattened document."""
    pack_mass = _mass_kg(flat.get("pack_mass_kg"))

    identity = BatteryIdentity(
        passport_id=to_str(flat.get("passport_id")),
        battery_id=to_str(flat.get("battery_id")),
        serial_number=to_str(flat.get("serial_number")),
        gtin=to_str(flat.get("gtin")),
        manufacturer=to_str(flat.get("manufacturer")),
        manufacturer_id=to_str(flat.get("manufacturer_id")),
        brand=to_str(flat.get("brand")),
        model_name=to_str(flat.get("model_name")),
        vehicle_model=to_str(flat.get("vehicle_model")),
        manufacturing_date=to_date(flat.get("manufacturing_date")),
        manufacturing_country=to_str(flat.get("manufacturing_country")),
        placed_on_market_date=to_date(flat.get("placed_on_market_date")),
        category=_category(flat.get("category")),
    )

    rated_kwh, rated_ah, remaining_kwh = resolve_capacities(flat)

    technical = BatteryTechnical(
        chemistry_raw=to_str(flat.get("chemistry_raw")),
        rated_capacity_kwh=rated_kwh,
        rated_capacity_ah=rated_ah,
        nominal_voltage_v=to_float(flat.get("nominal_voltage_v")),
        pack_mass_kg=pack_mass,
        module_count=to_int(flat.get("module_count")),
        cell_count=to_int(flat.get("cell_count")),
        cell_format=to_str(flat.get("cell_format")),
        warranty_years=to_float(flat.get("warranty_years")),
        expected_lifetime_cycles=to_int(flat.get("expected_lifetime_cycles")),
    )

    health = BatteryHealth(
        state_of_health_pct=to_float(flat.get("state_of_health_pct")),
        remaining_capacity_kwh=remaining_kwh,
        cycle_count=to_int(flat.get("cycle_count")),
        capacity_throughput_kwh=_energy_kwh(flat.get("capacity_throughput_kwh")),
        internal_resistance_mohm=to_float(flat.get("internal_resistance_mohm")),
        round_trip_efficiency_pct=to_float(flat.get("round_trip_efficiency_pct")),
        self_discharge_pct_per_month=to_float(
            flat.get("self_discharge_pct_per_month")
        ),
        cell_imbalance_mv=to_float(flat.get("cell_imbalance_mv")),
        deep_discharge_events=to_int(flat.get("deep_discharge_events")),
        over_temperature_events=to_int(flat.get("over_temperature_events")),
        measured_at=to_date(flat.get("measured_at")),
        condition=_condition(flat.get("condition")),
        safety_flags=_safety_flags(flat),
    )

    return BatteryPassport(
        identity=identity,
        technical=technical,
        health=health,
        composition=extract_composition(flat, document),
        raw=document,
    )


class GenericAdapter(PassportAdapter):
    """Last-resort adapter that works on any JSON document."""

    name = "generic"
    priority = -100

    def detect(self, document: dict[str, Any]) -> float:
        """Always applicable, but always the weakest candidate."""
        return 0.05 if isinstance(document, dict) and document else 0.0

    def parse(self, document: dict[str, Any]) -> BatteryPassport:
        """Flatten and extract."""
        passport = build_passport(FlatDocument(document), document)
        passport.source.adapter = self.name
        return passport
