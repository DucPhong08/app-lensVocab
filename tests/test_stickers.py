import io
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image

from app.schemas.flashcard import CropBox
from app.services import card_image_service as images
from app.services import sticker_service as stickers
from app.services.card_image_service import CardImageError, prepare_image


class StickerTests(unittest.TestCase):
    def fake_session(self):
        values = np.zeros((1, 1, 320, 320), dtype=np.float32)
        values[0, 0, 70:250, 90:230] = 1
        session = MagicMock()
        session.get_inputs.return_value = [SimpleNamespace(name="input")]
        session.run.return_value = [values]
        return session

    def test_cutout_has_transparent_exterior_opaque_object_and_white_outline(self):
        with patch.object(stickers, "_session", self.fake_session()):
            image = stickers.sticker(Image.new("RGB", (320, 320), "red"))
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        colors = image.get_flattened_data()
        self.assertTrue(any(r > 245 and g > 245 and b > 245 and a > 245 for r, g, b, a in colors))
        self.assertTrue(
            any(
                r > 245 and g < 10 and b < 10 and a > 245
                for r, g, b, a in image.get_flattened_data()
            )
        )

    def test_sticker_upload_processing_crops_before_segmentation_and_preserves_alpha(self):
        source = io.BytesIO()
        Image.new("RGB", (400, 200), "blue").save(source, "PNG")

        def segment(image):
            self.assertEqual(image.size, (100, 100))
            return Image.new("RGBA", image.size, (255, 0, 0, 0))

        with patch("app.services.card_image_service.sticker", side_effect=segment):
            output = prepare_image(
                source.getvalue(), CropBox(left=0.5, top=0, width=0.25, height=0.5), style="sticker"
            )
        image = Image.open(io.BytesIO(output))
        self.assertEqual(image.format, "PNG")
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getextrema()[-1], (0, 0))
        self.assertEqual(dict(image.getexif()), {})

    def test_missing_model_is_reported_without_silent_photo_fallback(self):
        source = io.BytesIO()
        Image.new("RGB", (10, 10), "blue").save(source, "PNG")
        with (
            patch.object(stickers, "_session", None),
            patch.object(
                stickers.settings, "IMAGE_SEGMENTATION_MODEL_PATH", "/nonexistent/u2netp.onnx"
            ),
            self.assertRaisesRegex(CardImageError, "STICKER_MODEL_NOT_CONFIGURED"),
        ):
            prepare_image(source.getvalue(), style="sticker")

    def test_modified_model_is_never_loaded(self):
        with tempfile.NamedTemporaryFile() as model:
            model.write(b"invalid-model")
            model.flush()
            with (
                patch.object(stickers, "_session", None),
                patch.object(stickers.settings, "IMAGE_SEGMENTATION_MODEL_PATH", model.name),
                self.assertRaisesRegex(stickers.StickerError, "STICKER_MODEL_INVALID"),
            ):
                stickers.sticker(Image.new("RGB", (10, 10)))

    def test_concurrent_sticker_request_is_rejected_before_decoding(self):
        images._sticker_gate.acquire()
        try:
            with self.assertRaisesRegex(CardImageError, "STICKER_BUSY"):
                prepare_image(b"not-even-decoded", style="sticker")
        finally:
            images._sticker_gate.release()
