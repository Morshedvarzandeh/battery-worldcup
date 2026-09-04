"""Local cache directory and checksum-verified downloads."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import requests

ENV_VAR = "BWC_CACHE_DIR"


def cache_dir() -> Path:
    """Return the cache root (``$BWC_CACHE_DIR`` or ``~/.cache/battery_worldcup``)."""
    root = Path(os.environ.get(ENV_VAR) or Path.home() / ".cache" / "battery_worldcup")
    root.mkdir(parents=True, exist_ok=True)
    return root


def sha256_of(path: str | Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    dest: str | Path,
    sha256: str | None = None,
    force: bool = False,
    timeout: float = 60.0,
) -> Path:
    """Stream ``url`` to ``dest`` and verify its SHA-256 when one is given.

    An existing file with a matching checksum is reused unless ``force`` is set.
    """
    dest = Path(dest)
    if dest.exists() and not force and (sha256 is None or sha256_of(dest) == sha256):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in response.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    if sha256 is not None:
        actual = sha256_of(tmp)
        if actual != sha256:
            tmp.unlink(missing_ok=True)
            raise ValueError(f"checksum mismatch for {url}: expected {sha256}, got {actual}")
    tmp.replace(dest)
    return dest
