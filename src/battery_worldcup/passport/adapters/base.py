"""Adapter contract and shared value-coercion helpers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any, ClassVar

from ..models import BatteryPassport

# Keys used by schemas that wrap a scalar together with its unit, e.g.
# {"value": 75, "unit": "kWh"}.
_VALUE_KEYS = ("value", "amount", "quantity", "val", "measurement", "magnitude")
_UNIT_KEYS = ("unit", "units", "uom", "unitcode", "unitofmeasure")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_NUMERIC = re.compile(r"-?\d+(?:[.,]\d+)?")


def normalise_key(text: str) -> str:
    """Lower-case a key and strip every non-alphanumeric character."""
    return _NON_ALNUM.sub("", str(text).lower())


def unwrap_value(raw: Any) -> tuple[Any, str | None]:
    """Split a possibly unit-wrapped value into ``(value, unit)``."""
    if isinstance(raw, dict):
        unit = None
        for key in raw:
            if normalise_key(key) in _UNIT_KEYS:
                unit = str(raw[key])
                break
        for key in raw:
            if normalise_key(key) in _VALUE_KEYS:
                return raw[key], unit
        return None, unit
    return raw, None


def to_float(raw: Any) -> float | None:
    """Coerce a value to float, tolerating units and thousands separators."""
    value, _ = unwrap_value(raw)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMERIC.search(str(value).replace(",", "."))
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def to_int(raw: Any) -> int | None:
    """Coerce a value to int."""
    value = to_float(raw)
    return int(value) if value is not None else None


def to_str(raw: Any) -> str | None:
    """Coerce a value to a non-empty string."""
    value, _ = unwrap_value(raw)
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def to_date(raw: Any) -> date | None:
    """Parse the date formats that turn up in passport exports."""
    value, _ = unwrap_value(raw)
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = to_str(value)
    if not text:
        return None

    candidate = text.strip().replace("Z", "+00:00")
    for parser in (
        date.fromisoformat,
        lambda t: datetime.fromisoformat(t).date(),
    ):
        try:
            return parser(candidate)  # type: ignore[operator]
        except ValueError:
            continue

    for pattern in ("%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue

    # A bare year-month, or a year on its own, still bounds the pack's age.
    match = re.match(r"^(\d{4})[-/](\d{1,2})$", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), 1)
    if re.match(r"^\d{4}$", text):
        return date(int(text), 1, 1)
    return None


class PassportAdapter(ABC):
    """Maps one source schema into a :class:`BatteryPassport`."""

    name: ClassVar[str] = "abstract"
    priority: ClassVar[int] = 0
    """Tie-break when several adapters report the same confidence."""

    @abstractmethod
    def detect(self, document: dict[str, Any]) -> float:
        """Confidence, 0-1, that this adapter understands ``document``."""

    @abstractmethod
    def parse(self, document: dict[str, Any]) -> BatteryPassport:
        """Convert ``document`` into a normalised passport."""
