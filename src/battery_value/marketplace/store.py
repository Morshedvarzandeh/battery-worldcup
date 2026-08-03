"""Listings and offers on disk.

Same shape as the valuation store: one SQLite file, no service to run, no
dependency to install. The two are deliberately separate databases -- a
deployment that must not retain customer valuations can still run a market,
and a market that closes leaves the valuation records intact.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Listing, ListingKind, ListingStatus, Offer, OfferStatus

logger = logging.getLogger(__name__)

_ENV_PATH = "BV_MARKET_PATH"
_ENV_ENABLED = "BV_MARKET_ENABLED"

# The same Crockford-style alphabet the valuation references use: no 0/O or
# 1/I, because these get read out over a phone and typed back in.
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_REFERENCE_LENGTH = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    reference             TEXT PRIMARY KEY,
    valuation_reference   TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    seller_handle         TEXT NOT NULL,
    region                TEXT,
    asking_price          REAL NOT NULL,
    currency              TEXT NOT NULL,
    kind                  TEXT NOT NULL,
    status                TEXT NOT NULL,
    title                 TEXT,
    description           TEXT,
    collection_only       INTEGER NOT NULL DEFAULT 1,
    battery_label         TEXT,
    rated_kwh             REAL,
    state_of_health       REAL,
    chemistry             TEXT,
    pack_model_key        TEXT,
    condition             TEXT,
    health_source         TEXT,
    estimate              REAL,
    valuation_confidence  REAL,
    wear_verdict          TEXT,
    wear_headline         TEXT,
    years_to_resale_floor REAL,
    sold_price            REAL,
    sold_at               TEXT
);
CREATE INDEX IF NOT EXISTS listings_status ON listings (status, created_at DESC);
CREATE INDEX IF NOT EXISTS listings_model ON listings (pack_model_key);
CREATE INDEX IF NOT EXISTS listings_chemistry ON listings (chemistry);
-- One *live* listing per valuation: the same pack advertised twice under two
-- assessments is exactly the confusion this market exists to remove. Withdrawn
-- and sold listings stay, because they are the price history.
DROP INDEX IF EXISTS listings_valuation;
CREATE UNIQUE INDEX IF NOT EXISTS listings_live_valuation
    ON listings (valuation_reference)
    WHERE status IN ('active', 'reserved');

CREATE TABLE IF NOT EXISTS offers (
    reference         TEXT PRIMARY KEY,
    listing_reference TEXT NOT NULL REFERENCES listings(reference) ON DELETE CASCADE,
    buyer_handle      TEXT NOT NULL,
    amount            REAL NOT NULL,
    currency          TEXT NOT NULL,
    message           TEXT,
    status            TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS offers_listing ON offers (listing_reference, amount DESC);
"""


def default_market_path() -> Path:
    """Where the market lives, honouring ``BV_MARKET_PATH`` then ``XDG_DATA_HOME``."""
    override = os.environ.get(_ENV_PATH)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return root / "battery-value" / "market.sqlite3"


def generate_reference(prefix: str) -> str:
    """A short reference, e.g. ``LS-7K2P-M4X9`` or ``OF-3QRT-8WBN``."""
    body = "".join(secrets.choice(_ALPHABET) for _ in range(_REFERENCE_LENGTH))
    return f"{prefix}-{body[:4]}-{body[4:]}"


def normalise_reference(raw: str, prefix: str) -> str:
    """Accept a reference however it was typed back in."""
    cleaned = "".join(character for character in str(raw).upper() if character.isalnum())
    if cleaned.startswith(prefix):
        cleaned = cleaned[len(prefix):]
    if len(cleaned) != _REFERENCE_LENGTH:
        return f"{prefix}-{cleaned}"  # let the lookup miss rather than guess
    return f"{prefix}-{cleaned[:4]}-{cleaned[4:]}"


class MarketStore:
    """SQLite-backed listings and offers.

    Args:
        path: Database file, created with its parent on first use.
        enabled: When false every method is a no-op.
    """

    def __init__(self, path: str | Path | None = None, *, enabled: bool = True) -> None:
        self.path = Path(path) if path is not None else default_market_path()
        self.enabled = enabled
        self._lock = threading.Lock()
        self._ready = False

    # -- plumbing ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
    def _row_to_listing(row: sqlite3.Row, offers: list[Offer]) -> Listing:
        return Listing(
            reference=row["reference"],
            valuation_reference=row["valuation_reference"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            seller_handle=row["seller_handle"],
            region=row["region"] or "",
            asking_price=float(row["asking_price"]),
            currency=row["currency"],
            battery_label=row["battery_label"] or "Battery",
            rated_kwh=float(row["rated_kwh"] or 0.0),
            state_of_health=float(row["state_of_health"] or 0.0),
            chemistry=row["chemistry"] or "",
            estimate=float(row["estimate"] or 0.0),
            pack_model_key=row["pack_model_key"],
            health_source=row["health_source"] or "measured",
            valuation_confidence=float(row["valuation_confidence"] or 0.0),
            wear_verdict=row["wear_verdict"] or "unknown",
            wear_headline=row["wear_headline"] or "",
            years_to_resale_floor=(
                float(row["years_to_resale_floor"])
                if row["years_to_resale_floor"] is not None
                else None
            ),
            condition=row["condition"] or "healthy",
            kind=ListingKind(row["kind"]),
            status=ListingStatus(row["status"]),
            title=row["title"] or "",
            description=row["description"] or "",
            collection_only=bool(row["collection_only"]),
            sold_price=(
                float(row["sold_price"]) if row["sold_price"] is not None else None
            ),
            sold_at=(
                datetime.fromisoformat(row["sold_at"]) if row["sold_at"] else None
            ),
            offers=offers,
        )

    @staticmethod
    def _row_to_offer(row: sqlite3.Row) -> Offer:
        return Offer(
            reference=row["reference"],
            listing_reference=row["listing_reference"],
            buyer_handle=row["buyer_handle"],
            amount=float(row["amount"]),
            currency=row["currency"],
            created_at=datetime.fromisoformat(row["created_at"]),
            status=OfferStatus(row["status"]),
            message=row["message"] or "",
        )

    def _offers_for(
        self, connection: sqlite3.Connection, references: list[str]
    ) -> dict[str, list[Offer]]:
        if not references:
            return {}
        placeholders = ",".join("?" * len(references))
        rows = connection.execute(
            f"SELECT * FROM offers WHERE listing_reference IN ({placeholders})"
            " ORDER BY amount DESC",
            references,
        ).fetchall()
        grouped: dict[str, list[Offer]] = {}
        for row in rows:
            grouped.setdefault(row["listing_reference"], []).append(
                self._row_to_offer(row)
            )
        return grouped

    # -- writing ----------------------------------------------------------

    def save(self, listing: Listing) -> Listing | None:
        """Insert or update a listing. Returns it, or ``None`` on failure."""
        if not self.enabled:
            return None
        listing.updated_at = datetime.now(timezone.utc)
        try:
            self._ensure_schema()
            with self._connect() as connection:
                # An upsert rather than INSERT OR REPLACE: the latter deletes
                # the row before reinserting it, and the offers' ON DELETE
                # CASCADE would take every bid on the listing with it. Accepting
                # an offer updates the listing, so this is not a corner case.
                connection.execute(
                    "INSERT INTO listings ("
                    "reference, valuation_reference, created_at, updated_at,"
                    "seller_handle, region, asking_price, currency, kind, status,"
                    "title, description, collection_only, battery_label, rated_kwh,"
                    "state_of_health, chemistry, pack_model_key, condition,"
                    "health_source, estimate, valuation_confidence, wear_verdict,"
                    "wear_headline, years_to_resale_floor, sold_price, sold_at"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(reference) DO UPDATE SET"
                    " updated_at=excluded.updated_at,"
                    " seller_handle=excluded.seller_handle,"
                    " region=excluded.region,"
                    " asking_price=excluded.asking_price,"
                    " kind=excluded.kind, status=excluded.status,"
                    " title=excluded.title, description=excluded.description,"
                    " collection_only=excluded.collection_only,"
                    " sold_price=excluded.sold_price, sold_at=excluded.sold_at",
                    (
                        listing.reference,
                        listing.valuation_reference,
                        listing.created_at.isoformat(),
                        listing.updated_at.isoformat(),
                        listing.seller_handle,
                        listing.region,
                        listing.asking_price,
                        listing.currency,
                        listing.kind.value,
                        listing.status.value,
                        listing.title,
                        listing.description,
                        int(listing.collection_only),
                        listing.battery_label,
                        listing.rated_kwh,
                        listing.state_of_health,
                        listing.chemistry,
                        listing.pack_model_key,
                        listing.condition,
                        listing.health_source,
                        listing.estimate,
                        listing.valuation_confidence,
                        listing.wear_verdict,
                        listing.wear_headline,
                        listing.years_to_resale_floor,
                        listing.sold_price,
                        listing.sold_at.isoformat() if listing.sold_at else None,
                    ),
                )
        except (sqlite3.Error, OSError, ValueError) as exc:
            logger.warning("could not save listing %s: %s", listing.reference, exc)
            return None
        return listing

    def save_offer(self, offer: Offer) -> Offer | None:
        """Insert or update an offer."""
        if not self.enabled:
            return None
        try:
            self._ensure_schema()
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO offers ("
                    "reference, listing_reference, buyer_handle, amount, currency,"
                    "message, status, created_at) VALUES (?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(reference) DO UPDATE SET"
                    " amount=excluded.amount, message=excluded.message,"
                    " status=excluded.status",
                    (
                        offer.reference,
                        offer.listing_reference,
                        offer.buyer_handle,
                        offer.amount,
                        offer.currency,
                        offer.message,
                        offer.status.value,
                        offer.created_at.isoformat(),
                    ),
                )
        except (sqlite3.Error, OSError, ValueError) as exc:
            logger.warning("could not save offer %s: %s", offer.reference, exc)
            return None
        return offer

    # -- reading ----------------------------------------------------------

    def get(self, reference: str) -> Listing | None:
        """One listing with its offers."""
        if not self.enabled:
            return None
        key = normalise_reference(reference, "LS")
        try:
            self._ensure_schema()
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM listings WHERE reference = ?", (key,)
                ).fetchone()
                if row is None:
                    return None
                offers = self._offers_for(connection, [key]).get(key, [])
        except (sqlite3.Error, OSError) as exc:
            logger.warning("could not read listing %s: %s", reference, exc)
            return None
        return self._row_to_listing(row, offers)

    def get_offer(self, reference: str) -> Offer | None:
        """One offer by reference."""
        if not self.enabled:
            return None
        try:
            self._ensure_schema()
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM offers WHERE reference = ?",
                    (normalise_reference(reference, "OF"),),
                ).fetchone()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("could not read offer %s: %s", reference, exc)
            return None
        return self._row_to_offer(row) if row else None

    def by_valuation(self, valuation_reference: str) -> Listing | None:
        """The listing created from a given valuation, if there is one.

        One valuation, one listing: the same pack advertised twice under two
        assessments is exactly the confusion this market exists to remove.
        """
        if not self.enabled:
            return None
        try:
            self._ensure_schema()
            with self._connect() as connection:
                # A valuation can have several listings over time -- withdrawn,
                # then relisted. The live one is what a caller means.
                row = connection.execute(
                    "SELECT * FROM listings WHERE valuation_reference = ?"
                    " ORDER BY (status IN ('active','reserved')) DESC,"
                    " created_at DESC LIMIT 1",
                    (valuation_reference,),
                ).fetchone()
                if row is None:
                    return None
                key = row["reference"]
                offers = self._offers_for(connection, [key]).get(key, [])
        except (sqlite3.Error, OSError) as exc:
            logger.warning("could not read listing for %s: %s", valuation_reference, exc)
            return None
        return self._row_to_listing(row, offers)

    def search(
        self,
        *,
        status: ListingStatus | None = ListingStatus.ACTIVE,
        chemistry: str | None = None,
        pack_model_key: str | None = None,
        region: str | None = None,
        minimum_kwh: float | None = None,
        minimum_soh: float | None = None,
        maximum_price: float | None = None,
        kind: ListingKind | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> list[Listing]:
        """Listings matching every supplied filter, newest first."""
        if not self.enabled:
            return []

        clauses: list[str] = []
        params: list[object] = []

        def where(sql: str, value: object) -> None:
            clauses.append(sql)
            params.append(value)

        if status is not None:
            where("status = ?", status.value)
        if kind is not None:
            where("kind = ?", kind.value)
        if chemistry:
            where("chemistry = ?", chemistry.upper())
        if pack_model_key:
            where("pack_model_key = ?", pack_model_key)
        if region:
            where("region = ?", region)
        if minimum_kwh is not None:
            where("rated_kwh >= ?", minimum_kwh)
        if minimum_soh is not None:
            where("state_of_health >= ?", minimum_soh)
        if maximum_price is not None:
            where("asking_price <= ?", maximum_price)
        if query:
            clauses.append("(battery_label LIKE ? OR title LIKE ? OR description LIKE ?)")
            params.extend([f"%{query}%"] * 3)

        sql = "SELECT * FROM listings"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))

        try:
            self._ensure_schema()
            with self._connect() as connection:
                rows = connection.execute(sql, params).fetchall()
                offers = self._offers_for(connection, [row["reference"] for row in rows])
        except (sqlite3.Error, OSError) as exc:
            logger.warning("could not search listings: %s", exc)
            return []
        return [
            self._row_to_listing(row, offers.get(row["reference"], [])) for row in rows
        ]

    def sold(self, limit: int = 200) -> list[Listing]:
        """Completed sales, newest first. The market's own price history."""
        return self.search(status=ListingStatus.SOLD, limit=limit)

    def offers_by_buyer(self, buyer_handle: str, limit: int = 50) -> list[Offer]:
        """Every offer one buyer has made."""
        if not self.enabled:
            return []
        try:
            self._ensure_schema()
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM offers WHERE buyer_handle = ?"
                    " ORDER BY created_at DESC LIMIT ?",
                    (buyer_handle, max(1, min(limit, 200))),
                ).fetchall()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("could not read offers for %s: %s", buyer_handle, exc)
            return []
        return [self._row_to_offer(row) for row in rows]

    def counts(self) -> dict[str, int]:
        """How many listings sit in each status."""
        if not self.enabled:
            return {}
        try:
            self._ensure_schema()
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT status, COUNT(*) AS n FROM listings GROUP BY status"
                ).fetchall()
        except (sqlite3.Error, OSError):
            return {}
        return {row["status"]: int(row["n"]) for row in rows}

    def delete(self, reference: str) -> bool:
        """Remove a listing and its offers."""
        if not self.enabled:
            return False
        try:
            self._ensure_schema()
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM listings WHERE reference = ?",
                    (normalise_reference(reference, "LS"),),
                )
                return bool(cursor.rowcount)
        except (sqlite3.Error, OSError) as exc:
            logger.warning("could not delete listing %s: %s", reference, exc)
            return False


_default: MarketStore | None = None


def default_market() -> MarketStore:
    """The process-wide market store, honouring ``BV_MARKET_ENABLED``."""
    global _default
    if _default is None:
        enabled = os.environ.get(_ENV_ENABLED, "1").lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        _default = MarketStore(enabled=enabled)
    return _default


def reset_default_market() -> None:
    """Drop the cached default store. Used by tests."""
    global _default
    _default = None


__all__ = [
    "MarketStore",
    "default_market",
    "default_market_path",
    "generate_reference",
    "normalise_reference",
    "reset_default_market",
]
