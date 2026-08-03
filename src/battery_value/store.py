"""Keep every valuation on record, so it can be handed over later.

A valuation is a point-in-time statement. Metal prices move weekly, so
re-running a scan next month gives a different number -- which is exactly the
wrong thing to do when a customer rings up asking about the figure they were
quoted. This stores the complete result at the moment it was produced, under a
short reference they can read out over the phone.

Retrieval never recomputes. The stored payload is the same one the API
returned, so the report rebuilt from it months later is what the customer was
originally shown.

Storage is a single SQLite file, which needs no service to run and no
dependency to install.

Note that records contain battery and vehicle identifiers. Retention defaults
to a year and is configurable; ``prune()`` enforces it.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ENV_STORE_PATH = "BV_STORE_PATH"
_ENV_STORE_ENABLED = "BV_STORE_ENABLED"
_ENV_RETENTION_DAYS = "BV_STORE_RETENTION_DAYS"

DEFAULT_RETENTION_DAYS = 365

# Crockford-style alphabet: no 0/O or 1/I, because these get read aloud down a
# phone line and typed back in by someone in a workshop.
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_REFERENCE_LENGTH = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS valuations (
    reference           TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    battery_label       TEXT,
    battery_id          TEXT,
    serial_number       TEXT,
    pack_model_key      TEXT,
    currency            TEXT,
    residual_value      REAL,
    recommended_pathway TEXT,
    confidence          REAL,
    payload             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS valuations_created_at ON valuations (created_at DESC);
CREATE INDEX IF NOT EXISTS valuations_serial ON valuations (serial_number);
CREATE INDEX IF NOT EXISTS valuations_battery ON valuations (battery_id);
"""


def default_store_path() -> Path:
    """Where records live, honouring ``BV_STORE_PATH`` then ``XDG_DATA_HOME``."""
    override = os.environ.get(_ENV_STORE_PATH)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return root / "battery-value" / "valuations.sqlite3"


def generate_reference() -> str:
    """A short reference a person can read aloud, e.g. ``BV-7K2P-M4X9``."""
    body = "".join(secrets.choice(_ALPHABET) for _ in range(_REFERENCE_LENGTH))
    return f"BV-{body[:4]}-{body[4:]}"


def normalise_reference(raw: str) -> str:
    """Accept a reference however it was typed back in.

    ``bv7k2pm4x9``, ``BV 7K2P M4X9`` and ``bv-7k2p-m4x9`` all mean the same
    thing to the person holding the paperwork, so they mean the same here.
    """
    cleaned = "".join(
        character for character in str(raw).upper() if character.isalnum()
    )
    if cleaned.startswith("BV"):
        cleaned = cleaned[2:]
    if len(cleaned) != _REFERENCE_LENGTH:
        return f"BV-{cleaned}"  # let the lookup miss rather than guess
    return f"BV-{cleaned[:4]}-{cleaned[4:]}"


@dataclass(frozen=True, slots=True)
class StoredValuation:
    """One valuation as it was produced, with its reference."""

    reference: str
    created_at: datetime
    battery_label: str
    currency: str
    residual_value: float
    confidence: float
    payload: dict[str, Any]
    battery_id: str | None = None
    serial_number: str | None = None
    pack_model_key: str | None = None
    recommended_pathway: str | None = None

    @property
    def age_days(self) -> int:
        """Whole days since this valuation was produced."""
        return max((datetime.now(timezone.utc) - self.created_at).days, 0)

    def summary_line(self) -> str:
        """One line for a listing."""
        return (
            f"{self.reference}  {self.created_at:%Y-%m-%d}  "
            f"{self.battery_label[:38]:<38s}  "
            f"{self.residual_value:>10,.0f} {self.currency}"
        )


class ValuationStore:
    """A SQLite-backed record of valuations produced.

    Args:
        path: Database file. Created, with its parent directory, on first use.
        retention_days: How long records are kept by :meth:`prune`.
        enabled: When false every method is a no-op, for deployments that
            must not retain customer data.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        retention_days: int | None = None,
        enabled: bool = True,
    ) -> None:
        self.path = Path(path) if path is not None else default_store_path()
        self.retention_days = (
            retention_days
            if retention_days is not None
            else int(os.environ.get(_ENV_RETENTION_DAYS, DEFAULT_RETENTION_DAYS))
        )
        self.enabled = enabled
        self._lock = threading.Lock()
        self._ready = False

    # -- plumbing ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
            self._ready = True

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> StoredValuation:
        return StoredValuation(
            reference=row["reference"],
            created_at=datetime.fromisoformat(row["created_at"]),
            battery_label=row["battery_label"] or "Battery",
            currency=row["currency"] or "EUR",
            residual_value=float(row["residual_value"] or 0.0),
            confidence=float(row["confidence"] or 0.0),
            payload=json.loads(row["payload"]),
            battery_id=row["battery_id"],
            serial_number=row["serial_number"],
            pack_model_key=row["pack_model_key"],
            recommended_pathway=row["recommended_pathway"],
        )

    # -- writing ----------------------------------------------------------

    def save(
        self, payload: dict[str, Any], *, passport: Any = None
    ) -> StoredValuation | None:
        """Store a serialised valuation and return the record.

        The reference is written back into ``payload`` so the response, the
        report and the record all carry the same one.

        Returns ``None`` when the store is disabled or the write fails: losing
        a record must never cost the customer their answer.
        """
        if not self.enabled:
            return None

        reference = payload.get("reference") or generate_reference()
        payload["reference"] = reference

        if passport is not None and "passport" not in payload:
            # The record has to stand on its own. A certificate reissued from it
            # months later must say the same things about the battery, and it
            # cannot if the passport it rested on is gone.
            from .serialisation import passport_to_dict

            try:
                payload["passport"] = passport_to_dict(passport)
            except Exception as exc:  # noqa: BLE001 - never lose the valuation
                logger.debug("could not embed the passport in %s: %s", reference, exc)

        battery = payload.get("battery", {})
        identity = getattr(passport, "identity", None)
        created_at = datetime.now(timezone.utc)

        record = StoredValuation(
            reference=reference,
            created_at=created_at,
            battery_label=battery.get("label", "Battery"),
            currency=payload.get("residual_value", {}).get("currency", "EUR"),
            residual_value=float(payload.get("residual_value", {}).get("amount", 0.0)),
            confidence=float(payload.get("confidence", 0.0)),
            payload=payload,
            battery_id=getattr(identity, "battery_id", None),
            serial_number=getattr(identity, "serial_number", None),
            pack_model_key=(battery.get("pack_model") or {}).get("key"),
            recommended_pathway=payload.get("recommended_pathway"),
        )

        try:
            self._ensure_schema()
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO valuations ("
                    "reference, created_at, battery_label, battery_id, serial_number,"
                    "pack_model_key, currency, residual_value, recommended_pathway,"
                    "confidence, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.reference,
                        created_at.isoformat(),
                        record.battery_label,
                        record.battery_id,
                        record.serial_number,
                        record.pack_model_key,
                        record.currency,
                        record.residual_value,
                        record.recommended_pathway,
                        record.confidence,
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )
        except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
            logger.warning("could not store valuation %s: %s", reference, exc)
            return None

        return record

    # -- reading ----------------------------------------------------------

    def get(self, reference: str) -> StoredValuation | None:
        """Look up one valuation by reference, however it was typed."""
        if not self.enabled:
            return None
        try:
            self._ensure_schema()
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM valuations WHERE reference = ?",
                    (normalise_reference(reference),),
                ).fetchone()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("could not read valuation %s: %s", reference, exc)
            return None
        return self._row_to_record(row) if row else None

    def recent(self, limit: int = 20) -> list[StoredValuation]:
        """The most recent valuations, newest first."""
        if not self.enabled:
            return []
        try:
            self._ensure_schema()
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM valuations ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(limit, 500)),),
                ).fetchall()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("could not list valuations: %s", exc)
            return []
        return [self._row_to_record(row) for row in rows]

    def find_by_battery(
        self, identifier: str, limit: int = 20
    ) -> list[StoredValuation]:
        """Every valuation for one pack, newest first.

        Matches on serial number or battery id, so a customer quoting the
        number stamped on their pack finds their history without a reference.
        """
        if not self.enabled or not identifier:
            return []
        try:
            self._ensure_schema()
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM valuations WHERE serial_number = ? OR battery_id = ?"
                    " ORDER BY created_at DESC LIMIT ?",
                    (identifier, identifier, max(1, min(limit, 500))),
                ).fetchall()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("could not search valuations: %s", exc)
            return []
        return [self._row_to_record(row) for row in rows]

    def count(self) -> int:
        """How many records are held."""
        if not self.enabled:
            return 0
        try:
            self._ensure_schema()
            with self._connect() as connection:
                return int(
                    connection.execute("SELECT COUNT(*) FROM valuations").fetchone()[0]
                )
        except (sqlite3.Error, OSError):
            return 0

    # -- housekeeping -----------------------------------------------------

    def prune(self, older_than_days: int | None = None) -> int:
        """Delete records past the retention period. Returns how many went."""
        if not self.enabled:
            return 0
        days = older_than_days if older_than_days is not None else self.retention_days
        if days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            self._ensure_schema()
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM valuations WHERE created_at < ?", (cutoff,)
                )
                return cursor.rowcount or 0
        except (sqlite3.Error, OSError) as exc:
            logger.warning("could not prune valuations: %s", exc)
            return 0

    def delete(self, reference: str) -> bool:
        """Remove one record, for a customer asking to be forgotten."""
        if not self.enabled:
            return False
        try:
            self._ensure_schema()
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM valuations WHERE reference = ?",
                    (normalise_reference(reference),),
                )
                return bool(cursor.rowcount)
        except (sqlite3.Error, OSError) as exc:
            logger.warning("could not delete valuation %s: %s", reference, exc)
            return False


_default: ValuationStore | None = None


def default_store() -> ValuationStore:
    """The process-wide store, honouring ``BV_STORE_ENABLED``."""
    global _default
    if _default is None:
        enabled = os.environ.get(_ENV_STORE_ENABLED, "1").lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        _default = ValuationStore(enabled=enabled)
    return _default


def reset_default_store() -> None:
    """Drop the cached default store. Used by tests."""
    global _default
    _default = None
