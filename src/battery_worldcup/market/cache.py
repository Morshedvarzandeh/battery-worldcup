"""A small on-disk cache so repeated valuations do not hammer price APIs.

Deliberately dependency-free and forgiving: a corrupt or unwritable cache
degrades to a cache miss rather than breaking a valuation.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ENV_CACHE_DIR = "BWC_CACHE_DIR"
DEFAULT_TTL_SECONDS = 60 * 60


def default_cache_dir() -> Path:
    """Cache location, honouring ``BWC_CACHE_DIR`` then ``XDG_CACHE_HOME``."""
    override = os.environ.get(_ENV_CACHE_DIR)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return root / "battery-worldcup"


@dataclass
class PriceCache:
    """A JSON-file cache keyed by arbitrary strings."""

    directory: Path
    enabled: bool = True

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)

    def _path_for(self, key: str) -> Path:
        safe = "".join(
            character if character.isalnum() or character in "-_." else "_"
            for character in key
        )
        return self.directory / f"{safe}.json"

    def get(self, key: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Any | None:
        """Return the cached payload, or ``None`` when missing, stale or unreadable."""
        if not self.enabled:
            return None
        path = self._path_for(key)
        try:
            with path.open(encoding="utf-8") as fh:
                envelope = json.load(fh)
            if time.time() - float(envelope["stored_at"]) > ttl_seconds:
                return None
            return envelope["payload"]
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError) as exc:
            logger.debug("cache read failed for %s: %s", key, exc)
            return None

    def set(self, key: str, payload: Any) -> None:
        """Store ``payload`` under ``key``. Failures are logged, never raised."""
        if not self.enabled:
            return
        path = self._path_for(key)
        envelope = {"stored_at": time.time(), "payload": payload}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # Write via a temp file so a crash mid-write cannot leave a
            # half-written cache entry behind.
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.directory,
                prefix=path.stem,
                suffix=".tmp",
                delete=False,
            ) as fh:
                json.dump(envelope, fh)
                temp_path = Path(fh.name)
            temp_path.replace(path)
        except (OSError, TypeError, ValueError) as exc:
            logger.debug("cache write failed for %s: %s", key, exc)

    def clear(self) -> int:
        """Delete every cache entry. Returns how many files were removed."""
        removed = 0
        try:
            for path in self.directory.glob("*.json"):
                path.unlink(missing_ok=True)
                removed += 1
        except OSError as exc:
            logger.debug("cache clear failed: %s", exc)
        return removed


_default: PriceCache | None = None


def default_cache() -> PriceCache:
    """The process-wide default cache."""
    global _default
    if _default is None:
        _default = PriceCache(default_cache_dir())
    return _default


def disabled_cache() -> PriceCache:
    """A cache that never stores or returns anything. Useful in tests."""
    return PriceCache(default_cache_dir(), enabled=False)
