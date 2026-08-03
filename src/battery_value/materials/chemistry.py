"""Cell chemistry identification and default material intensities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

from ..errors import BatteryValueError

_DATA_PACKAGE = "battery_value.materials.data"


class UnknownChemistryError(BatteryValueError):
    """The passport's chemistry string matched no known chemistry."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        super().__init__(
            f"could not identify cell chemistry from {raw!r}; "
            "pass an explicit chemistry key or declare composition in the passport"
        )


@dataclass(frozen=True, slots=True)
class ChemistrySpec:
    """Everything the valuation needs to know about a cell chemistry."""

    key: str
    label: str
    family: str
    anode: str
    aliases: tuple[str, ...]
    typical_pack_kg_per_kwh: float
    typical_cell_wh_per_kg: float
    typical_cycle_life_to_80pct: int
    material_intensity_kg_per_kwh: dict[str, float]
    second_life_suitability: float
    cathode_formula: str | None = None
    notes: str = ""

    @property
    def payable_elements(self) -> tuple[str, ...]:
        """Elements this chemistry contributes to recovery revenue."""
        return tuple(self.material_intensity_kg_per_kwh)

    def contains_nickel_or_cobalt(self) -> bool:
        """Whether the chemistry carries the metals that drive recycling value."""
        intensity = self.material_intensity_kg_per_kwh
        return intensity.get("Ni", 0.0) > 0.0 or intensity.get("Co", 0.0) > 0.0


@dataclass(frozen=True, slots=True)
class ChemistryLibrary:
    """The loaded chemistry dataset."""

    updated: str
    basis: str
    notes: tuple[str, ...]
    sources: tuple[str, ...]
    specs: dict[str, ChemistrySpec] = field(default_factory=dict)

    def get(self, key: str) -> ChemistrySpec:
        """Fetch a chemistry by its canonical key."""
        try:
            return self.specs[key.strip().upper()]
        except KeyError:
            raise UnknownChemistryError(key) from None

    def keys(self) -> tuple[str, ...]:
        """All canonical chemistry keys."""
        return tuple(self.specs)


def _load_json(filename: str) -> dict[str, Any]:
    with resources.files(_DATA_PACKAGE).joinpath(filename).open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def load_chemistries() -> ChemistryLibrary:
    """Load and cache the bundled chemistry dataset."""
    raw = _load_json("chemistries.json")
    specs: dict[str, ChemistrySpec] = {}
    for key, entry in raw["chemistries"].items():
        specs[key] = ChemistrySpec(
            key=key,
            label=entry["label"],
            family=entry["family"],
            anode=entry.get("anode", "graphite"),
            aliases=tuple(entry.get("aliases", ())),
            typical_pack_kg_per_kwh=float(entry["typical_pack_kg_per_kwh"]),
            typical_cell_wh_per_kg=float(entry["typical_cell_wh_per_kg"]),
            typical_cycle_life_to_80pct=int(entry["typical_cycle_life_to_80pct"]),
            material_intensity_kg_per_kwh=dict(entry["material_intensity_kg_per_kwh"]),
            second_life_suitability=float(entry.get("second_life_suitability", 0.5)),
            cathode_formula=entry.get("cathode_formula"),
            notes=entry.get("notes", ""),
        )
    return ChemistryLibrary(
        updated=raw["updated"],
        basis=raw["basis"],
        notes=tuple(raw.get("notes", ())),
        sources=tuple(raw.get("sources", ())),
        specs=specs,
    )


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    """Normalised alias -> canonical key, including the keys themselves."""
    index: dict[str, str] = {}
    for key, spec in load_chemistries().specs.items():
        index[_normalise(key)] = key
        index[_normalise(spec.label)] = key
        for alias in spec.aliases:
            index[_normalise(alias)] = key
        if spec.cathode_formula:
            index[_normalise(spec.cathode_formula)] = key
    return index


def resolve_chemistry(raw: str | ChemistrySpec | None) -> ChemistrySpec:
    """Identify a chemistry from whatever string a passport happens to carry.

    Passports in the wild write the same chemistry a dozen ways -- ``NMC811``,
    ``NCM-811``, ``Li-NMC 811``, ``lithium nickel manganese cobalt oxide``. This
    resolves all of them, falling back to substring matching before giving up.

    >>> resolve_chemistry("Li-NMC 811").key
    'NMC811'
    >>> resolve_chemistry("LiFePO4").key
    'LFP'
    """
    if isinstance(raw, ChemistrySpec):
        return raw
    if raw is None or not str(raw).strip():
        raise UnknownChemistryError(str(raw))

    text = str(raw)
    index = _alias_index()
    normalised = _normalise(text)

    if normalised in index:
        return load_chemistries().get(index[normalised])

    # Longest-alias-first substring match, so "NMC811" beats a bare "NMC" hit
    # inside the same string.
    for alias in sorted(index, key=len, reverse=True):
        if len(alias) >= 3 and alias in normalised:
            return load_chemistries().get(index[alias])

    # Last resort: an NMC/NCM ratio written with separators, e.g. "NMC 6-2-2".
    ratio = re.search(r"(?:nmc|ncm)\D*(\d)\D*(\d)\D*(\d)", text, flags=re.IGNORECASE)
    if ratio:
        candidate = f"NMC{''.join(ratio.groups())}"
        if _normalise(candidate) in index:
            return load_chemistries().get(index[_normalise(candidate)])

    raise UnknownChemistryError(text)


def try_resolve_chemistry(raw: str | ChemistrySpec | None) -> ChemistrySpec | None:
    """Like :func:`resolve_chemistry` but returns ``None`` instead of raising."""
    try:
        return resolve_chemistry(raw)
    except UnknownChemistryError:
        return None
