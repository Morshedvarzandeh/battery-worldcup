"""Decode what a battery passport QR code actually contains.

Under EU Regulation 2023/1542 the data carrier on the pack is a QR code, but
the regulation does not fix the payload. In practice a scan yields one of:

* an HTTPS URL to the passport document;
* a GS1 Digital Link, which is a URL with identifiers encoded in its path;
* an inline JSON document, occasionally base64-encoded in a ``data:`` URI;
* a bare identifier (URN, DID or an OEM part/serial string).

This module classifies the payload and pulls out every identifier it can, so
the resolver knows whether to fetch, decode or look up.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from ..errors import UnknownCarrierError


class CarrierKind(str, Enum):
    """How a scanned payload should be interpreted."""

    URL = "url"
    GS1_DIGITAL_LINK = "gs1_digital_link"
    INLINE_JSON = "inline_json"
    DATA_URI = "data_uri"
    URN = "urn"
    IDENTIFIER = "identifier"


# GS1 Application Identifiers that turn up in battery Digital Links.
GS1_APPLICATION_IDENTIFIERS: dict[str, str] = {
    "00": "sscc",
    "01": "gtin",
    "10": "batch",
    "21": "serial",
    "22": "cpv",
    "240": "additional_id",
    "241": "customer_part_number",
    "253": "gdti",
    "254": "glnextension",
    "8003": "grai",
    "8004": "giai",
    "8006": "itip",
    "8010": "cpid",
    "8018": "gsrn",
}

_URN_PATTERN = re.compile(r"^(urn:[a-z0-9][a-z0-9-]{0,31}:.+|did:[a-z0-9]+:.+)$", re.I)
_DATA_URI_PATTERN = re.compile(
    r"^data:(?P<mime>[\w.+-]+/[\w.+-]+)?(?P<b64>;base64)?,(?P<payload>.*)$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(slots=True)
class CarrierPayload:
    """A classified QR payload."""

    kind: CarrierKind
    raw: str
    url: str | None = None
    identifiers: dict[str, str] = field(default_factory=dict)
    inline_document: dict[str, Any] | None = None

    @property
    def is_fetchable(self) -> bool:
        """Whether resolving this payload requires an HTTP request."""
        return self.url is not None and self.inline_document is None

    @property
    def primary_identifier(self) -> str | None:
        """The most specific identifier available, preferring the unit serial."""
        for key in ("serial", "giai", "gtin", "battery_id", "passport_id", "id"):
            if key in self.identifiers:
                return self.identifiers[key]
        return next(iter(self.identifiers.values()), None)


def _try_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        parsed = json.loads(stripped)
    except ValueError:
        return None
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed[0]
    return None


def _parse_data_uri(text: str) -> dict[str, Any] | None:
    match = _DATA_URI_PATTERN.match(text.strip())
    if match is None:
        return None
    payload = match.group("payload")
    if match.group("b64"):
        try:
            # Restore padding some encoders strip.
            padded = payload + "=" * (-len(payload) % 4)
            payload = base64.b64decode(padded).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
    else:
        payload = unquote(payload)
    return _try_json(payload)


def _parse_gs1_path(path: str) -> dict[str, str]:
    """Extract AI/value pairs from a GS1 Digital Link path."""
    segments = [segment for segment in path.split("/") if segment]
    identifiers: dict[str, str] = {}
    index = 0
    while index < len(segments) - 1:
        candidate = segments[index]
        if candidate in GS1_APPLICATION_IDENTIFIERS:
            identifiers[GS1_APPLICATION_IDENTIFIERS[candidate]] = unquote(
                segments[index + 1]
            )
            index += 2
        else:
            index += 1
    return identifiers


def parse_carrier(payload: str) -> CarrierPayload:
    """Classify a scanned payload.

    >>> parse_carrier("https://id.gs1.org/01/09506000134376/21/AB123").kind
    <CarrierKind.GS1_DIGITAL_LINK: 'gs1_digital_link'>
    >>> parse_carrier('{"batteryId": "X"}').kind
    <CarrierKind.INLINE_JSON: 'inline_json'>

    Raises:
        UnknownCarrierError: If the payload is empty or unusable.
    """
    if payload is None or not str(payload).strip():
        raise UnknownCarrierError("empty QR payload")

    text = str(payload).strip()

    inline = _try_json(text)
    if inline is not None:
        return CarrierPayload(
            kind=CarrierKind.INLINE_JSON, raw=text, inline_document=inline
        )

    if text.lower().startswith("data:"):
        document = _parse_data_uri(text)
        if document is None:
            raise UnknownCarrierError("data: URI did not contain readable JSON")
        return CarrierPayload(
            kind=CarrierKind.DATA_URI, raw=text, inline_document=document
        )

    if _URN_PATTERN.match(text):
        return CarrierPayload(
            kind=CarrierKind.URN, raw=text, identifiers={"passport_id": text}
        )

    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        identifiers = _parse_gs1_path(parsed.path)
        identifiers.update(
            {
                key.lower(): value
                for key, value in parse_qsl(parsed.query)
                if key.lower()
                in {"id", "serial", "battery_id", "batteryid", "passport_id", "gtin"}
            }
        )
        kind = (
            CarrierKind.GS1_DIGITAL_LINK
            if any(
                key in identifiers
                for key in ("gtin", "giai", "grai", "sscc", "gdti", "itip")
            )
            else CarrierKind.URL
        )
        return CarrierPayload(kind=kind, raw=text, url=text, identifiers=identifiers)

    # Anything left is treated as an opaque identifier a lookup layer may know.
    return CarrierPayload(
        kind=CarrierKind.IDENTIFIER, raw=text, identifiers={"battery_id": text}
    )
