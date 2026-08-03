"""Adapters for the passport schemas we can recognise by structure.

Each one reuses the generic flattening extractor -- which already copes with
nesting -- but reports high confidence and applies schema-specific fixes the
generic pass cannot infer.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import BatteryPassport
from .base import PassportAdapter, normalise_key
from .generic import FlatDocument, build_passport


def _document_keys(document: dict[str, Any]) -> set[str]:
    """Every normalised key anywhere in the document."""
    keys: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                keys.add(normalise_key(key))
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(document)
    return keys


def _marker_score(document: dict[str, Any], markers: tuple[str, ...]) -> float:
    """Share of ``markers`` present in the document, as a 0-1 confidence."""
    keys = _document_keys(document)
    hits = sum(1 for marker in markers if marker in keys)
    return hits / len(markers) if markers else 0.0


class EuDppAdapter(PassportAdapter):
    """EU digital battery passport, per Regulation 2023/1542 Annex XIII.

    Recognised by its top-level section structure: general information, carbon
    footprint, supply-chain due diligence, material composition, circularity
    and performance/durability.
    """

    name = "eu_dpp"
    priority = 100

    MARKERS = (
        "generalinformation",
        "carbonfootprint",
        "materialcomposition",
        "circularity",
        "performanceanddurability",
        "supplychainduediligence",
        "batterypassport",
    )

    def detect(self, document: dict[str, Any]) -> float:
        """Confidence based on how many Annex XIII sections are present."""
        score = _marker_score(document, self.MARKERS)
        # Two distinct Annex XIII sections is already a strong signal.
        return min(1.0, score * 2.2) if score >= 2 / len(self.MARKERS) else score

    def parse(self, document: dict[str, Any]) -> BatteryPassport:
        """Extract, then apply Annex XIII conventions."""
        flat = FlatDocument(document)
        passport = build_passport(flat, document)
        passport.source.adapter = self.name

        # Annex XIII declares critical raw materials as a share of battery mass.
        # The generic pass only reads those when the key names the unit, so
        # re-read the material composition section explicitly.
        if passport.composition.is_empty:
            section = _find_section(document, ("materialcomposition", "composition"))
            if section is not None:
                from .generic import extract_composition

                passport.composition = extract_composition(FlatDocument(section), section)

        return passport


class GbaAdapter(PassportAdapter):
    """Global Battery Alliance battery passport."""

    name = "gba"
    priority = 90

    MARKERS = (
        "batteryidentification",
        "gbaid",
        "batterypassportid",
        "esgperformance",
        "batterymaterials",
        "humanrightsindex",
    )

    def detect(self, document: dict[str, Any]) -> float:
        """Confidence from GBA-specific section names."""
        score = _marker_score(document, self.MARKERS)
        context = json.dumps(document.get("@context", ""))[:400].lower()
        if "gbaglobal" in context or "globalbattery" in context:
            return max(score, 0.9)
        return min(1.0, score * 2.0)

    def parse(self, document: dict[str, Any]) -> BatteryPassport:
        """Extract via the generic flattener."""
        passport = build_passport(FlatDocument(document), document)
        passport.source.adapter = self.name
        return passport


class NativeAdapter(PassportAdapter):
    """This module's own serialised :class:`BatteryPassport`."""

    name = "native"
    priority = 120

    def detect(self, document: dict[str, Any]) -> float:
        """Confidence that this is a round-tripped native passport."""
        top_level = {normalise_key(key) for key in document}
        required = {"identity", "technical", "health"}
        overlap = len(required & top_level)
        if overlap == len(required):
            return 1.0
        return 0.4 if overlap >= 2 else 0.0

    def parse(self, document: dict[str, Any]) -> BatteryPassport:
        """Validate straight back into the model."""
        passport = BatteryPassport.model_validate(document)
        passport.source.adapter = self.name
        if passport.raw is None:
            passport.raw = document
        return passport


def _find_section(document: Any, names: tuple[str, ...]) -> dict[str, Any] | None:
    """Depth-first search for a nested section by normalised key name."""
    if isinstance(document, dict):
        for key, child in document.items():
            if normalise_key(key) in names and isinstance(child, (dict, list)):
                return child if isinstance(child, dict) else {"items": child}
        for child in document.values():
            found = _find_section(child, names)
            if found is not None:
                return found
    elif isinstance(document, list):
        for child in document:
            found = _find_section(child, names)
            if found is not None:
                return found
    return None
