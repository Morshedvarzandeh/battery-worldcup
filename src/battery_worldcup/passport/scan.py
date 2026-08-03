"""Decode QR codes from images.

Server-side decoding is optional. The browser UI decodes with the platform
``BarcodeDetector`` API and posts the payload as text, which keeps the common
path dependency-free; this module exists for CLI use and image uploads.

Install the extra with ``pip install 'battery-worldcup[scan]'``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..errors import PassportError

logger = logging.getLogger(__name__)


class ScanUnavailableError(PassportError):
    """Image decoding was requested but no decoder backend is installed."""

    def __init__(self) -> None:
        super().__init__(
            "no QR decoder available; install with: pip install 'battery-worldcup[scan]' "
            "(or decode client-side and pass the payload text instead)"
        )


def decoder_available() -> bool:
    """Whether an image-decoding backend can be imported."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def decode_image(path: str | Path) -> list[str]:
    """Decode every QR code in an image file.

    Args:
        path: Path to a PNG/JPEG image containing one or more QR codes.

    Returns:
        The decoded payload strings, possibly empty.

    Raises:
        ScanUnavailableError: If no decoder backend is installed.
        PassportError: If the image cannot be read.
    """
    try:
        import cv2
    except ImportError as exc:
        raise ScanUnavailableError() from exc

    image_path = Path(path)
    if not image_path.exists():
        raise PassportError(f"image not found: {image_path}")

    image = cv2.imread(str(image_path))
    if image is None:
        raise PassportError(f"could not read image: {image_path}")

    detector = cv2.QRCodeDetector()
    ok, decoded, _, _ = detector.detectAndDecodeMulti(image)
    if ok and decoded:
        payloads = [text for text in decoded if text]
        if payloads:
            return payloads

    # Fall back to the single-code path, which sometimes succeeds where the
    # multi-code detector does not.
    single, _, _ = detector.detectAndDecode(image)
    return [single] if single else []


def decode_image_bytes(data: bytes) -> list[str]:
    """Decode QR codes from raw image bytes, e.g. an HTTP upload."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise ScanUnavailableError() from exc

    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise PassportError("uploaded bytes were not a readable image")

    detector = cv2.QRCodeDetector()
    ok, decoded, _, _ = detector.detectAndDecodeMulti(image)
    if ok and decoded:
        payloads = [text for text in decoded if text]
        if payloads:
            return payloads

    single, _, _ = detector.detectAndDecode(image)
    return [single] if single else []
