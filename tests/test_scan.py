"""QR decoding from photos: the main way a phone user reaches this module.

A sticker on a battery pack gets photographed at an angle, in a workshop, by
someone holding a phone in one hand. These tests degrade a clean render in the
ways that actually happens and check it still reads.
"""

from __future__ import annotations

import io
import json

import pytest

from battery_value.errors import PassportError
from battery_value.passport.scan import decoder_available

pytestmark = pytest.mark.skipif(
    not decoder_available(), reason="image decoding needs the [scan] or [api] extra"
)

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
qrcode = pytest.importorskip("qrcode")

from battery_value.passport.scan import (  # noqa: E402
    _rotate_keeping_corners,
    decode_image,
    decode_image_bytes,
)

# What a real data carrier holds: a link, not a whole document.
STICKER_URL = "https://id.gs1.org/01/09506000134376/21/AB123"


def render(payload: str, box_size: int = 10):
    """Render a QR code the way a label printer would."""
    code = qrcode.QRCode(box_size=box_size, border=4)
    code.add_data(payload)
    code.make(fit=True)
    buffer = io.BytesIO()
    code.make_image().save(buffer, format="PNG")
    return cv2.imdecode(np.frombuffer(buffer.getvalue(), np.uint8), cv2.IMREAD_COLOR)


def to_png(image) -> bytes:
    return cv2.imencode(".png", image)[1].tobytes()


def dim(image, factor: float):
    """Reduce contrast, as a photo under workshop lighting would."""
    return np.clip(
        image.astype(np.float32) * factor + (1 - factor) * 128, 0, 255
    ).astype(np.uint8)


@pytest.fixture(scope="module")
def sticker():
    return render(STICKER_URL)


class TestCleanDecoding:
    def test_round_trip(self, sticker):
        assert decode_image_bytes(to_png(sticker)) == [STICKER_URL]

    @pytest.mark.parametrize("length", [40, 120, 320, 401, 700, 1200])
    def test_payload_lengths(self, length):
        """Decoding must not fail on particular QR versions."""
        payload = "https://dpp.example.com/b/" + "A" * max(0, length - 26)
        assert decode_image_bytes(to_png(render(payload))) == [payload]

    def test_full_passport_document_in_one_code(self, eu_dpp_document):
        payload = json.dumps(eu_dpp_document, separators=(",", ":"))
        assert decode_image_bytes(to_png(render(payload))) == [payload]


class TestPhotoConditions:
    """Each case is a way a real phone photo goes wrong."""

    @pytest.mark.parametrize("degrees", [12, 30, 45, 90])
    def test_rotated(self, sticker, degrees):
        rotated = _rotate_keeping_corners(sticker, degrees)
        assert decode_image_bytes(to_png(rotated)) == [STICKER_URL]

    def test_small_in_frame(self, sticker):
        small = cv2.resize(sticker, None, fx=0.4, fy=0.4)
        assert decode_image_bytes(to_png(small)) == [STICKER_URL]

    def test_out_of_focus(self, sticker):
        blurred = cv2.GaussianBlur(sticker, (5, 5), 0)
        assert decode_image_bytes(to_png(blurred)) == [STICKER_URL]

    def test_poor_lighting(self, sticker):
        assert decode_image_bytes(to_png(dim(sticker, 0.35))) == [STICKER_URL]

    def test_poor_lighting_and_soft_focus(self, sticker):
        degraded = cv2.GaussianBlur(dim(sticker, 0.45), (3, 3), 0)
        assert decode_image_bytes(to_png(degraded)) == [STICKER_URL]

    def test_heavy_jpeg_compression(self, sticker):
        encoded = cv2.imencode(".jpg", sticker, [cv2.IMWRITE_JPEG_QUALITY, 30])[1]
        recompressed = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        assert decode_image_bytes(to_png(recompressed)) == [STICKER_URL]

    def test_sensor_noise(self, sticker):
        rng = np.random.default_rng(7)
        noisy = np.clip(
            sticker.astype(np.int16) + rng.normal(0, 18, sticker.shape), 0, 255
        ).astype(np.uint8)
        assert decode_image_bytes(to_png(noisy)) == [STICKER_URL]

    def test_angled_and_soft(self, sticker):
        degraded = cv2.GaussianBlur(_rotate_keeping_corners(sticker, 20), (3, 3), 0)
        assert decode_image_bytes(to_png(degraded)) == [STICKER_URL]


class TestFailureHandling:
    def test_no_code_in_image_returns_empty(self):
        blank = np.full((300, 300, 3), 255, dtype=np.uint8)
        assert decode_image_bytes(to_png(blank)) == []

    def test_unreadable_bytes_raise(self):
        with pytest.raises(PassportError, match="not a readable image"):
            decode_image_bytes(b"definitely not an image")

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(PassportError, match="not found"):
            decode_image(tmp_path / "absent.png")

    def test_file_round_trip(self, tmp_path, sticker):
        path = tmp_path / "code.png"
        path.write_bytes(to_png(sticker))
        assert decode_image(path) == [STICKER_URL]


class TestRotationHelper:
    @pytest.mark.parametrize("degrees", [15, 30, 45, 90])
    def test_canvas_grows_so_corners_survive(self, sticker, degrees):
        """Clipping a code's finder patterns makes it permanently undecodable."""
        grey = cv2.cvtColor(sticker, cv2.COLOR_BGR2GRAY)
        rotated = _rotate_keeping_corners(grey, degrees)
        assert rotated.shape[0] >= grey.shape[0]
        assert rotated.shape[1] >= grey.shape[1]
