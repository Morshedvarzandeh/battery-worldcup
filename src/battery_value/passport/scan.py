"""Decode QR codes from images.

Server-side decoding is optional. The browser UI decodes with the platform
``BarcodeDetector`` API and posts the payload as text, which keeps the common
path dependency-free; this module exists for CLI use and image uploads.

Install the extra with ``pip install 'battery-value[scan]'``.
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
            "no QR decoder available; install with: pip install 'battery-value[scan]' "
            "(or decode client-side and pass the payload text instead)"
        )


def decoder_available() -> bool:
    """Whether an image-decoding backend can be imported."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def _decode_array(image) -> list[str]:
    """Decode QR codes from a loaded image, retrying with preprocessing.

    A single detector pass is unreliable in exactly the situation this module
    cares about: a photo taken on a phone, at an angle, in poor light. It also
    fails outright on certain QR versions even from a clean render. So each
    variant below is tried in turn until one reads, cheapest first.
    """
    import cv2

    detector = cv2.QRCodeDetector()

    def attempt(candidate) -> list[str]:
        try:
            ok, decoded, _, _ = detector.detectAndDecodeMulti(candidate)
            if ok and decoded:
                found = [text for text in decoded if text]
                if found:
                    return found
            # The single-code path sometimes succeeds where the multi-code
            # detector gives up, so it is a separate attempt rather than a
            # replacement.
            single, _, _ = detector.detectAndDecode(candidate)
            return [single] if single else []
        except cv2.error:
            return []

    found = attempt(image)
    if found:
        return found

    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    for variant in _preprocessing_variants(grey):
        found = attempt(variant)
        if found:
            return found

    # Last resort: the detector can miss a dense code photographed at an angle,
    # so re-present it at a few orientations. Only reached when everything
    # above has failed, so it costs nothing on a clean scan.
    for angle in _ROTATION_SWEEP:
        found = attempt(_rotate_keeping_corners(grey, angle))
        if found:
            return found
    return []


# Enough orientations to bring an angled photo close to square, without
# turning a failed decode into a slow one.
_ROTATION_SWEEP = (-15, 15, -30, 30, 45)


def _rotate_keeping_corners(grey, degrees: float):
    """Rotate an image, growing the canvas so no corner is clipped.

    Rotating within the original frame can cut off a QR code's finder
    patterns, which is the one thing guaranteed to make it undecodable.
    """
    import cv2
    import numpy as np

    height, width = grey.shape[:2]
    radians = np.radians(degrees)
    cos, sin = abs(np.cos(radians)), abs(np.sin(radians))
    new_width = int(height * sin + width * cos)
    new_height = int(height * cos + width * sin)

    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 1.0)
    matrix[0, 2] += new_width / 2 - width / 2
    matrix[1, 2] += new_height / 2 - height / 2
    return cv2.warpAffine(
        grey, matrix, (new_width, new_height), borderValue=255
    )


def _preprocessing_variants(grey):
    """Progressively more aggressive cleanups of a greyscale image."""
    import cv2

    yield grey

    # Upscaling helps when the code is small in frame, which is the usual
    # outcome of photographing a sticker from a comfortable distance.
    height, width = grey.shape[:2]
    if max(height, width) < 2000:
        yield cv2.resize(grey, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)

    # Otsu handles even lighting; adaptive handles a shadow across the pack.
    _, otsu = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield otsu

    yield cv2.adaptiveThreshold(
        grey, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 5
    )

    # A gentle sharpen recovers slightly out-of-focus captures.
    blurred = cv2.GaussianBlur(grey, (0, 0), 3)
    yield cv2.addWeighted(grey, 1.6, blurred, -0.6, 0)


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

    return _decode_array(image)


def decode_image_bytes(data: bytes) -> list[str]:
    """Decode QR codes from raw image bytes, e.g. a photo uploaded from a phone."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise ScanUnavailableError() from exc

    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise PassportError("uploaded bytes were not a readable image")

    return _decode_array(image)
