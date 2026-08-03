"""Adapter registry and schema detection."""

from __future__ import annotations

from typing import Any

from ...errors import PassportError
from ..models import BatteryPassport
from .base import PassportAdapter
from .generic import GenericAdapter
from .schemas import EuDppAdapter, GbaAdapter, NativeAdapter

DEFAULT_ADAPTERS: tuple[PassportAdapter, ...] = (
    NativeAdapter(),
    EuDppAdapter(),
    GbaAdapter(),
    GenericAdapter(),
)


def detect_adapter(
    document: dict[str, Any],
    adapters: tuple[PassportAdapter, ...] = DEFAULT_ADAPTERS,
) -> tuple[PassportAdapter, float]:
    """Pick the adapter most likely to understand ``document``.

    Returns the adapter and its confidence. Ties break on adapter priority, so
    a specific schema always beats the generic fallback.
    """
    if not isinstance(document, dict) or not document:
        raise PassportError("passport document must be a non-empty JSON object")

    scored = [(adapter, adapter.detect(document)) for adapter in adapters]
    scored = [(adapter, score) for adapter, score in scored if score > 0]
    if not scored:
        raise PassportError("no adapter could interpret the passport document")

    scored.sort(key=lambda item: (item[1], item[0].priority), reverse=True)
    return scored[0]


def parse_document(
    document: dict[str, Any],
    adapters: tuple[PassportAdapter, ...] = DEFAULT_ADAPTERS,
) -> BatteryPassport:
    """Parse a passport document with the best-matching adapter."""
    adapter, _ = detect_adapter(document, adapters)
    return adapter.parse(document)


__all__ = [
    "DEFAULT_ADAPTERS",
    "EuDppAdapter",
    "GbaAdapter",
    "GenericAdapter",
    "NativeAdapter",
    "PassportAdapter",
    "detect_adapter",
    "parse_document",
]
