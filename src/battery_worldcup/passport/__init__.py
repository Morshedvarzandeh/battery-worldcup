"""Battery passport ingestion: scan, classify, fetch and normalise."""

from .adapters import DEFAULT_ADAPTERS, PassportAdapter, detect_adapter, parse_document
from .models import (
    BatteryCategory,
    BatteryComposition,
    BatteryHealth,
    BatteryIdentity,
    BatteryPassport,
    BatteryTechnical,
    PackCondition,
    PassportCompleteness,
    PassportSource,
)
from .qr import CarrierKind, CarrierPayload, parse_carrier
from .resolver import PassportFetchError, PassportLookup, PassportResolver

__all__ = [
    "DEFAULT_ADAPTERS",
    "BatteryCategory",
    "BatteryComposition",
    "BatteryHealth",
    "BatteryIdentity",
    "BatteryPassport",
    "BatteryTechnical",
    "CarrierKind",
    "CarrierPayload",
    "PackCondition",
    "PassportAdapter",
    "PassportCompleteness",
    "PassportFetchError",
    "PassportLookup",
    "PassportResolver",
    "PassportSource",
    "detect_adapter",
    "parse_carrier",
    "parse_document",
]
