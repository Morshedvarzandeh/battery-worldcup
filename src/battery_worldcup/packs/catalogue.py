"""The bundled pack catalogue: loading, component synthesis and matching."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from importlib import resources
from typing import Any

from ..passport.models import BatteryPassport
from .models import PackComponent, PackMatch, PackModel

_DATA_PACKAGE = "battery_worldcup.packs.data"
_DATA_FILE = "pack_models.json"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# A pack whose energy is within this fraction of a catalogue entry is treated as
# the same model, absorbing the usual gross/net and rounding differences.
_ENERGY_TOLERANCE = 0.06

MATCH_THRESHOLD = 0.55


def _normalise(text: str | None) -> str:
    return _NON_ALNUM.sub("", (text or "").lower())


def _tokens(text: str | None) -> set[str]:
    return {token for token in _NON_ALNUM.split((text or "").lower()) if len(token) > 1}


@lru_cache(maxsize=1)
def _raw_catalogue() -> dict[str, Any]:
    with resources.files(_DATA_PACKAGE).joinpath(_DATA_FILE).open(
        encoding="utf-8"
    ) as fh:
        return json.load(fh)


def synthesise_components(
    *,
    pack_mass_kg: float,
    module_count: int | None,
    used_module_value_eur: float,
    templates: dict[str, Any],
    mass_split: dict[str, float],
) -> tuple[PackComponent, ...]:
    """Build a component list from the archetype mass split.

    Used for models with no published teardown. The split is deliberately
    coarse but keeps the pack's mass balance intact, which is what the
    parts-out and recycling pathways actually consume.
    """
    components: list[PackComponent] = []

    for group, share in mass_split.items():
        if group == "notes" or not isinstance(share, (int, float)):
            continue
        template = templates.get(group, {})
        group_mass = pack_mass_kg * float(share)

        if group == "modules":
            count = module_count or 1
            components.append(
                PackComponent(
                    key="modules",
                    label=template.get("label", "Battery modules"),
                    count=count,
                    unit_mass_kg=group_mass / count,
                    reusable=bool(template.get("reusable", True)),
                    dominant_material=template.get("dominant_material"),
                    unit_value_eur=used_module_value_eur,
                    dismantling_minutes_each=float(
                        template.get("dismantling_minutes_each", 10.0)
                    ),
                    note=template.get("note", ""),
                )
            )
            continue

        components.append(
            PackComponent(
                key=group,
                label=template.get("label", group.replace("_", " ").title()),
                count=1,
                unit_mass_kg=group_mass,
                reusable=bool(template.get("reusable", False)),
                dominant_material=template.get("dominant_material"),
                unit_value_eur=float(template.get("unit_value_eur", 0.0)),
                dismantling_minutes_each=float(
                    template.get("dismantling_minutes_each", 15.0)
                ),
                note=template.get("note", ""),
            )
        )

    return tuple(components)


def _build_model(entry: dict[str, Any], catalogue: dict[str, Any]) -> PackModel:
    templates = catalogue.get("component_templates", {}).get("groups", {})
    mass_split = catalogue.get("default_mass_split", {})

    if entry.get("components"):
        components = tuple(
            PackComponent(
                key=component["key"],
                label=component.get("label", component["key"]),
                count=int(component.get("count", 1)),
                unit_mass_kg=float(component.get("unit_mass_kg", 0.0)),
                reusable=bool(component.get("reusable", False)),
                dominant_material=component.get("dominant_material"),
                unit_value_eur=float(component.get("unit_value_eur", 0.0)),
                dismantling_minutes_each=float(
                    component.get("dismantling_minutes_each", 15.0)
                ),
                note=component.get("note", ""),
            )
            for component in entry["components"]
        )
    else:
        components = synthesise_components(
            pack_mass_kg=float(entry["pack_mass_kg"]),
            module_count=entry.get("module_count"),
            used_module_value_eur=float(entry.get("used_module_value_eur", 0.0)),
            templates=templates,
            mass_split=mass_split,
        )

    years = entry.get("years")
    return PackModel(
        key=entry["key"],
        label=entry["label"],
        manufacturer=entry["manufacturer"],
        chemistry=entry["chemistry"],
        rated_kwh=float(entry["rated_kwh"]),
        pack_mass_kg=float(entry["pack_mass_kg"]),
        vehicle_models=tuple(entry.get("vehicle_models", ())),
        aliases=tuple(entry.get("aliases", ())),
        years=(int(years[0]), int(years[1])) if years else None,
        module_count=entry.get("module_count"),
        cell_count=entry.get("cell_count"),
        nominal_voltage_v=entry.get("nominal_voltage_v"),
        cell_format=entry.get("cell_format"),
        used_module_value_eur=float(entry.get("used_module_value_eur", 0.0)),
        oem_replacement_price_eur_per_kwh=entry.get("oem_replacement_price_eur_per_kwh"),
        second_life_demand=entry.get("second_life_demand", "medium"),
        confidence=entry.get("confidence", "medium"),
        notes=entry.get("notes", ""),
        components=components,
    )


@dataclass(frozen=True, slots=True)
class PackCatalogue:
    """An indexed set of pack models."""

    models: tuple[PackModel, ...]
    labour_rate_eur_per_hour: float = 68.0
    fixed_setup_minutes: float = 45.0

    def get(self, key: str) -> PackModel | None:
        """Fetch a model by catalogue key."""
        for model in self.models:
            if model.key == key:
                return model
        return None

    def match(self, passport: BatteryPassport) -> PackMatch | None:
        """Best catalogue entry for a passport, or ``None`` below threshold."""
        candidates = [self._score(model, passport) for model in self.models]
        candidates = [match for match in candidates if match is not None]
        if not candidates:
            return None
        best = max(candidates, key=lambda match: match.score)
        return best if best.score >= MATCH_THRESHOLD else None

    def match_all(self, passport: BatteryPassport, limit: int = 5) -> list[PackMatch]:
        """Ranked candidate matches, for diagnostics and UI disambiguation."""
        scored = [self._score(model, passport) for model in self.models]
        ranked = sorted(
            (match for match in scored if match is not None),
            key=lambda match: match.score,
            reverse=True,
        )
        return ranked[:limit]

    def _score(self, model: PackModel, passport: BatteryPassport) -> PackMatch | None:
        """Score how well ``model`` explains ``passport``."""
        identity = passport.identity
        reasons: list[str] = []
        score = 0.0

        haystack = _normalise(
            " ".join(
                filter(
                    None,
                    (
                        identity.model_name,
                        identity.vehicle_model,
                        identity.brand,
                        identity.manufacturer,
                    ),
                )
            )
        )

        # An explicit alias or catalogue key in the passport is decisive.
        for alias in (model.key, *model.aliases):
            if alias and _normalise(alias) and _normalise(alias) in haystack:
                score += 0.65
                reasons.append(f"alias '{alias}'")
                break

        # A named vehicle model is nearly as strong.
        for vehicle in model.vehicle_models:
            normalised = _normalise(vehicle)
            if normalised and normalised in haystack:
                score += 0.55
                reasons.append(f"vehicle '{vehicle}'")
                break
        else:
            # Fall back to token overlap for looser spellings. A single
            # distinctive token ("i3", "Zoe") is weaker evidence than two, but
            # combined with maker and energy it still identifies a pack.
            passport_tokens = _tokens(identity.vehicle_model) | _tokens(
                identity.model_name
            )
            best_overlap: set[str] = set()
            for vehicle in model.vehicle_models:
                overlap = _tokens(vehicle) & passport_tokens
                if len(overlap) > len(best_overlap):
                    best_overlap = overlap
            if len(best_overlap) >= 2:
                score += 0.35
                reasons.append(f"tokens {sorted(best_overlap)}")
            elif len(best_overlap) == 1:
                score += 0.20
                reasons.append(f"token {sorted(best_overlap)[0]!r}")

        manufacturer_match = bool(
            _normalise(model.manufacturer)
            and _normalise(model.manufacturer)
            in _normalise(f"{identity.manufacturer or ''}{identity.brand or ''}")
        )
        if manufacturer_match:
            score += 0.20
            reasons.append("manufacturer")

        rated = passport.rated_kwh
        if rated and model.rated_kwh:
            deviation = abs(rated - model.rated_kwh) / model.rated_kwh
            if deviation <= _ENERGY_TOLERANCE:
                score += 0.25 if manufacturer_match else 0.15
                reasons.append(f"energy {rated:g} kWh")
            elif deviation > 0.25:
                # A pack of a clearly different size is not this model.
                score -= 0.45

        chemistry = passport.technical.chemistry
        if chemistry and _normalise(chemistry.key) == _normalise(model.chemistry):
            score += 0.10
            reasons.append("chemistry")

        mass = passport.technical.pack_mass_kg
        if mass and model.pack_mass_kg:
            if abs(mass - model.pack_mass_kg) / model.pack_mass_kg <= 0.10:
                score += 0.10
                reasons.append("mass")

        if score <= 0:
            return None
        return PackMatch(model=model, score=min(score, 1.0), matched_on=tuple(reasons))


@lru_cache(maxsize=1)
def load_catalogue() -> PackCatalogue:
    """Load and cache the bundled pack catalogue."""
    raw = _raw_catalogue()
    labour = raw.get("labour", {})
    return PackCatalogue(
        models=tuple(_build_model(entry, raw) for entry in raw["models"]),
        labour_rate_eur_per_hour=float(labour.get("rate_eur_per_hour", 68.0)),
        fixed_setup_minutes=float(labour.get("fixed_setup_minutes", 45.0)),
    )


def catalogue_from_documents(entries: list[dict[str, Any]]) -> PackCatalogue:
    """Build a catalogue from caller-supplied model documents.

    Lets an operator maintain their own fleet catalogue in the same JSON shape
    as the bundled file, without forking the package.
    """
    raw = _raw_catalogue()
    models = [
        replace(_build_model(entry, raw), source=entry.get("source", "custom"))
        for entry in entries
    ]
    labour = raw.get("labour", {})
    return PackCatalogue(
        models=tuple(models),
        labour_rate_eur_per_hour=float(labour.get("rate_eur_per_hour", 68.0)),
        fixed_setup_minutes=float(labour.get("fixed_setup_minutes", 45.0)),
    )
