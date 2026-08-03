"""Read prices from a local CSV.

The battery-metal price indices that matter most -- lithium carbonate, cobalt
and nickel sulphate, black mass payables -- are all licensed products from
Fastmarkets, Benchmark Mineral Intelligence, SMM or Argus. Their terms do not
permit redistributing the numbers, and none offers an open API.

The workable answer for a subscriber is therefore to export their assessments
and point this provider at the file. It keeps licensed data on the customer's
own infrastructure while still feeding the valuation.

Expected columns (header row required, extra columns ignored)::

    form,price,currency,unit,as_of,source_detail
    lithium_carbonate,12400,USD,t,2026-07-30,Fastmarkets MB-LI-0029
    cobalt_sulphate,8350,USD,t,2026-07-30,Fastmarkets MB-CO-0004
"""

from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path

from ..types import PriceQuality, PriceQuote
from .base import PriceProvider

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = frozenset({"form", "price", "currency", "unit", "as_of"})


class CsvOverrideProvider(PriceProvider):
    """Loads assessments a subscriber has exported to CSV."""

    key = "csv"
    label = "Local CSV assessments"
    quality = PriceQuality.BENCHMARK
    requires_network = False

    def __init__(self, path: str | Path, quality: PriceQuality | None = None) -> None:
        self.path = Path(path)
        self._quality = quality or self.quality
        self._quotes: dict[str, PriceQuote] | None = None

    def is_available(self) -> bool:
        """Available when the CSV exists and parses to at least one quote."""
        return bool(self._load())

    def supported_forms(self) -> frozenset[str]:
        """Forms present in the CSV."""
        return frozenset(self._load())

    def fetch(self, form: str) -> PriceQuote | None:
        """Return the CSV's quote for ``form``."""
        return self._load().get(form)

    def reload(self) -> None:
        """Drop the parsed cache so the next call re-reads the file."""
        self._quotes = None

    def _load(self) -> dict[str, PriceQuote]:
        if self._quotes is not None:
            return self._quotes

        quotes: dict[str, PriceQuote] = {}
        try:
            with self.path.open(newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
                if missing:
                    logger.warning(
                        "%s is missing required columns: %s",
                        self.path,
                        ", ".join(sorted(missing)),
                    )
                    self._quotes = {}
                    return self._quotes

                for line_number, row in enumerate(reader, start=2):
                    quote = self._parse_row(row, line_number)
                    if quote is not None:
                        quotes[quote.form] = quote
        except FileNotFoundError:
            logger.debug("price CSV not found: %s", self.path)
        except OSError as exc:
            logger.warning("could not read price CSV %s: %s", self.path, exc)

        self._quotes = quotes
        return quotes

    def _parse_row(self, row: dict[str, str], line_number: int) -> PriceQuote | None:
        form = (row.get("form") or "").strip()
        if not form:
            return None
        try:
            return PriceQuote(
                form=form,
                price=float(row["price"]),
                currency=row["currency"].strip(),
                unit=row["unit"].strip(),
                as_of=date.fromisoformat(row["as_of"].strip()),
                source=self.key,
                quality=self._quality,
                source_detail=(row.get("source_detail") or "").strip()
                or f"from {self.path.name}",
            )
        except (KeyError, ValueError) as exc:
            logger.warning("%s line %d is malformed: %s", self.path, line_number, exc)
            return None
