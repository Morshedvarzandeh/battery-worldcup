"""Turn a scan into a passport: classify, fetch, parse, normalise.

Fetching a URL that arrived on a sticker is untrusted input, so the HTTP path
here is deliberately defensive: HTTPS-ish schemes only, no private or loopback
address ranges by default, a response size cap and a short timeout.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..errors import PassportError, UnknownCarrierError
from .adapters import DEFAULT_ADAPTERS, PassportAdapter, detect_adapter
from .models import BatteryPassport, PassportSource
from .qr import CarrierKind, CarrierPayload, parse_carrier

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_JSON_LD_PATTERN = re.compile(
    r'<script[^>]+type=["\']application/(?:ld\+)?json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


class PassportFetchError(PassportError):
    """The passport document could not be retrieved."""


def _is_private_host(hostname: str) -> bool:
    """Whether a hostname resolves to a private, loopback or link-local address."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # Unresolvable: let the HTTP layer fail rather than guessing.
        return False
    for info in infos:
        address = info[4][0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_reserved
            or parsed.is_multicast
        ):
            return True
    return False


def _extract_embedded_json(html: str) -> dict[str, Any] | None:
    """Find a JSON-LD payload inside an HTML passport page."""
    for match in _JSON_LD_PATTERN.finditer(html):
        try:
            parsed = json.loads(match.group(1).strip())
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed:
            return parsed
        if isinstance(parsed, list):
            for entry in parsed:
                if isinstance(entry, dict) and entry:
                    return entry
    return None


@dataclass
class PassportResolver:
    """Resolves scanned payloads into normalised passports.

    Args:
        adapters: Schema adapters to try, best-match wins.
        lookups: Extra layers consulted when a payload carries only an
            identifier, or when the fetched document is incomplete. See
            :mod:`battery_worldcup.packs` for the pack-model layer.
        allow_private_hosts: Permit fetching from private/loopback addresses.
            Off by default; enable only for a trusted internal passport host.
    """

    adapters: tuple[PassportAdapter, ...] = DEFAULT_ADAPTERS
    lookups: list[PassportLookup] = field(default_factory=list)
    timeout: float = 12.0
    allow_private_hosts: bool = False
    client: httpx.Client | None = None
    user_agent: str = "battery-worldcup/0.1 (+passport-resolver)"

    def from_qr(self, payload: str) -> BatteryPassport:
        """Resolve a scanned QR payload end to end."""
        carrier = parse_carrier(payload)
        return self.from_carrier(carrier)

    def from_carrier(self, carrier: CarrierPayload) -> BatteryPassport:
        """Resolve an already-classified carrier payload."""
        document: dict[str, Any] | None = carrier.inline_document

        if document is None and carrier.is_fetchable:
            document = self.fetch(carrier.url or "")

        if document is None:
            passport = self._from_lookups(carrier)
            if passport is None:
                raise UnknownCarrierError(
                    f"nothing could resolve {carrier.kind.value} payload "
                    f"{carrier.raw[:80]!r}; no document to fetch and no lookup "
                    "layer recognised the identifier"
                )
        else:
            passport = self.from_document(document)

        passport.source = PassportSource(
            kind="qr",
            reference=carrier.url or carrier.primary_identifier or carrier.raw[:120],
            adapter=passport.source.adapter,
            retrieved_at=datetime.now(timezone.utc),
            verified=False,
        )
        self._apply_carrier_identifiers(passport, carrier)
        return passport

    def from_document(self, document: dict[str, Any]) -> BatteryPassport:
        """Parse an already-retrieved passport document."""
        adapter, confidence = detect_adapter(document, self.adapters)
        logger.debug("using adapter %s (confidence %.2f)", adapter.name, confidence)
        passport = adapter.parse(document)
        if passport.source.kind == "unknown":
            passport.source.kind = "inline"
        passport.source.adapter = adapter.name
        return passport

    def from_file(self, path: str | Path) -> BatteryPassport:
        """Load a passport from a local JSON file."""
        file_path = Path(path)
        try:
            document = json.loads(file_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PassportError(f"passport file not found: {file_path}") from exc
        except ValueError as exc:
            raise PassportError(f"{file_path} is not valid JSON: {exc}") from exc

        if not isinstance(document, dict):
            raise PassportError(f"{file_path} must contain a JSON object")

        passport = self.from_document(document)
        passport.source = PassportSource(
            kind="file",
            reference=str(file_path),
            adapter=passport.source.adapter,
            retrieved_at=datetime.now(timezone.utc),
        )
        return passport

    def from_image(self, path: str | Path) -> BatteryPassport:
        """Decode a QR code from an image file and resolve it."""
        from .scan import decode_image

        payloads = decode_image(path)
        if not payloads:
            raise PassportError(f"no QR code found in {path}")
        return self.from_qr(payloads[0])

    def fetch(self, url: str) -> dict[str, Any] | None:
        """Retrieve a passport document over HTTP.

        Returns ``None`` when the response holds no usable JSON, so the caller
        can fall through to the lookup layers.
        """
        self._check_url(url)
        headers = {
            "Accept": "application/json, application/ld+json;q=0.9, text/html;q=0.5",
            "User-Agent": self.user_agent,
        }
        try:
            if self.client is not None:
                response = self.client.get(url, headers=headers, timeout=self.timeout)
            else:
                response = httpx.get(
                    url, headers=headers, timeout=self.timeout, follow_redirects=True
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PassportFetchError(f"could not fetch passport from {url}: {exc}") from exc

        content = response.content[:MAX_RESPONSE_BYTES]
        text = content.decode(response.encoding or "utf-8", errors="replace")

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return parsed[0]
        except ValueError:
            pass

        return _extract_embedded_json(text)

    def _check_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise PassportFetchError(f"refusing non-HTTP passport URL: {url!r}")
        if not parsed.hostname:
            raise PassportFetchError(f"passport URL has no host: {url!r}")
        if not self.allow_private_hosts and _is_private_host(parsed.hostname):
            raise PassportFetchError(
                f"refusing to fetch {parsed.hostname!r}: resolves to a private address. "
                "Set allow_private_hosts=True for a trusted internal passport host."
            )

    def _from_lookups(self, carrier: CarrierPayload) -> BatteryPassport | None:
        for lookup in self.lookups:
            try:
                passport = lookup.lookup(carrier)
            except Exception as exc:  # noqa: BLE001 - a bad layer must not break the scan
                logger.warning("lookup layer %s failed: %s", lookup, exc)
                continue
            if passport is not None:
                return passport
        return None

    @staticmethod
    def _apply_carrier_identifiers(
        passport: BatteryPassport, carrier: CarrierPayload
    ) -> None:
        """Fill identity gaps from identifiers encoded in the carrier itself."""
        identity = passport.identity
        mapping = {
            "serial": "serial_number",
            "gtin": "gtin",
            "battery_id": "battery_id",
            "passport_id": "passport_id",
            "giai": "battery_id",
        }
        for carrier_key, field_name in mapping.items():
            value = carrier.identifiers.get(carrier_key)
            if value and not getattr(identity, field_name, None):
                setattr(identity, field_name, value)


class PassportLookup:
    """A layer that can supply battery data from an identifier alone.

    Implement this to plug in a fleet database, an OEM service or any other
    registry that knows more about a pack than its QR code does.
    """

    def lookup(self, carrier: CarrierPayload) -> BatteryPassport | None:
        """Return a passport for ``carrier``, or ``None`` if unknown."""
        raise NotImplementedError


__all__ = [
    "CarrierKind",
    "PassportFetchError",
    "PassportLookup",
    "PassportResolver",
]
