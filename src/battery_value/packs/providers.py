"""Pluggable layers that know things about battery packs.

The bundled catalogue will never cover every pack in the world. These layers
let an operator add their own sources -- a fleet database, an OEM service API,
a dismantler's own model list -- and have them consulted before the bundled
data, exactly like the price provider chain.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import httpx

from ..passport.models import BatteryPassport
from .catalogue import PackCatalogue, catalogue_from_documents, load_catalogue
from .models import PackMatch

logger = logging.getLogger(__name__)

_ENV_PACK_DIR = "BV_PACK_CATALOGUE_DIR"
_ENV_PACK_API = "BV_PACK_API_URL"


class PackDataProvider(ABC):
    """A source of pack model data."""

    key: ClassVar[str] = "abstract"
    label: ClassVar[str] = "Abstract pack provider"

    def is_available(self) -> bool:
        """Whether this layer can answer right now."""
        return True

    @abstractmethod
    def find(self, passport: BatteryPassport) -> PackMatch | None:
        """Best pack model for ``passport``, or ``None``."""

    def find_safe(self, passport: BatteryPassport) -> PackMatch | None:
        """:meth:`find` with a blanket guard, used by the resolver."""
        try:
            return self.find(passport)
        except Exception as exc:  # noqa: BLE001 - one bad layer must not break a scan
            logger.warning("pack provider %s failed: %s", self.key, exc)
            return None

    def describe(self) -> str:
        """One-liner for diagnostics."""
        status = "available" if self.is_available() else "unavailable"
        return f"{self.key} ({self.label}) [{status}]"


class BundledCatalogueProvider(PackDataProvider):
    """The pack catalogue shipped with the package."""

    key = "bundled"
    label = "Bundled pack catalogue"

    def __init__(self, catalogue: PackCatalogue | None = None) -> None:
        self._catalogue = catalogue

    @property
    def catalogue(self) -> PackCatalogue:
        """The catalogue this layer serves."""
        return self._catalogue if self._catalogue is not None else load_catalogue()

    def find(self, passport: BatteryPassport) -> PackMatch | None:
        """Match against the bundled models."""
        return self.catalogue.match(passport)


class JsonDirectoryProvider(PackDataProvider):
    """Reads extra pack models from a directory of JSON files.

    Each file holds either a single model object or a list of them, in the same
    shape as the bundled catalogue's ``models`` entries. This is the simplest
    way to extend coverage without touching the package.
    """

    key = "json_dir"
    label = "Local pack model directory"

    def __init__(self, directory: str | Path | None = None) -> None:
        resolved = directory or os.environ.get(_ENV_PACK_DIR)
        self.directory = Path(resolved) if resolved else None
        self._catalogue: PackCatalogue | None = None

    def is_available(self) -> bool:
        """Available when the directory exists and holds at least one model."""
        return bool(self._load() and self._load().models)

    def reload(self) -> None:
        """Drop the parsed cache so the next call re-reads the directory."""
        self._catalogue = None

    def _load(self) -> PackCatalogue | None:
        if self._catalogue is not None:
            return self._catalogue
        if self.directory is None or not self.directory.is_dir():
            return None

        entries: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("skipping unreadable pack model file %s: %s", path, exc)
                continue
            if isinstance(document, dict) and "models" in document:
                document = document["models"]
            if isinstance(document, dict):
                entries.append(document)
            elif isinstance(document, list):
                entries.extend(item for item in document if isinstance(item, dict))

        valid = [entry for entry in entries if _has_required_fields(entry)]
        for entry in entries:
            if entry not in valid:
                logger.warning(
                    "pack model %r is missing required fields and was skipped",
                    entry.get("key", "<unkeyed>"),
                )

        self._catalogue = catalogue_from_documents(valid) if valid else None
        return self._catalogue

    def find(self, passport: BatteryPassport) -> PackMatch | None:
        """Match against the local models."""
        catalogue = self._load()
        return catalogue.match(passport) if catalogue else None


class HttpPackProvider(PackDataProvider):
    """Queries an HTTP service for pack model data.

    The service is expected to accept the passport identity as query parameters
    and return either a single model object or ``{"models": [...]}``.
    """

    key = "http"
    label = "Remote pack model service"

    def __init__(
        self,
        url: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = url or os.environ.get(_ENV_PACK_API)
        self.headers = headers or {}
        self.timeout = timeout
        self.client = client

    def is_available(self) -> bool:
        """Available once a service URL is configured."""
        return bool(self.url)

    def find(self, passport: BatteryPassport) -> PackMatch | None:
        """Ask the remote service about this pack."""
        if not self.url:
            return None

        identity = passport.identity
        params = {
            key: value
            for key, value in {
                "manufacturer": identity.manufacturer or identity.brand,
                "model": identity.model_name,
                "vehicle_model": identity.vehicle_model,
                "gtin": identity.gtin,
                "rated_kwh": passport.rated_kwh,
            }.items()
            if value
        }

        if self.client is not None:
            response = self.client.get(
                self.url, params=params, headers=self.headers, timeout=self.timeout
            )
        else:
            response = httpx.get(
                self.url,
                params=params,
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
        response.raise_for_status()
        document = response.json()

        entries = document.get("models") if isinstance(document, dict) else document
        if isinstance(document, dict) and "models" not in document:
            entries = [document]
        if not entries:
            return None

        valid = [
            entry
            for entry in entries
            if isinstance(entry, dict) and _has_required_fields(entry)
        ]
        if not valid:
            logger.warning("%s returned no usable pack models", self.url)
            return None

        return catalogue_from_documents(valid).match(passport)


@dataclass
class PackResolver:
    """Consults pack data layers in order and returns the first confident match."""

    providers: list[PackDataProvider] = field(default_factory=list)

    def find(self, passport: BatteryPassport) -> PackMatch | None:
        """Best pack model across all layers."""
        best: PackMatch | None = None
        for provider in self.providers:
            if not provider.is_available():
                continue
            match = provider.find_safe(passport)
            if match is None:
                continue
            # A confident hit from an earlier (more specific) layer wins
            # outright; otherwise keep the strongest candidate seen.
            if match.is_confident:
                return match
            if best is None or match.score > best.score:
                best = match
        return best

    def describe_chain(self) -> list[str]:
        """One line per layer, for the API's diagnostics route."""
        return [provider.describe() for provider in self.providers]


def _has_required_fields(entry: dict[str, Any]) -> bool:
    required = ("key", "label", "manufacturer", "chemistry", "rated_kwh", "pack_mass_kg")
    return all(entry.get(field) is not None for field in required)


def build_pack_resolver(
    *,
    directory: str | Path | None = None,
    api_url: str | None = None,
    extra_providers: list[PackDataProvider] | None = None,
) -> PackResolver:
    """Assemble the standard pack-data chain.

    Ordering, most specific first: caller-supplied layers, then a local model
    directory, then a remote service, then battery-data if it is configured,
    then the bundled catalogue.

    The bundled catalogue sits last and is a generated cache of battery-data
    rather than a rival source of truth -- it is what keeps a fresh clone
    working with nothing configured.
    """
    providers: list[PackDataProvider] = list(extra_providers or [])

    local = JsonDirectoryProvider(directory)
    if local.is_available():
        providers.append(local)

    remote = HttpPackProvider(api_url)
    if remote.is_available():
        providers.append(remote)

    # Imported here: battery-data support is optional and pulls in psycopg.
    from .battery_data import battery_data_providers

    providers.extend(battery_data_providers())

    providers.append(BundledCatalogueProvider())
    return PackResolver(providers=providers)
