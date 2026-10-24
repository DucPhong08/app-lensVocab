from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from app.config import settings

MODEL_SHA256 = "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8"
_session = None
_lock = threading.Lock()


class StickerError(Exception):
    pass


def sticker(image: Image.Image) -> Image.Image:
    """Segment the selected crop locally, then outline its alpha silhouette."""
    import numpy as np
    import onnxruntime as ort

    global _session
    with _lock:
        if _session is None:
            path = Path(settings.IMAGE_SEGMENTATION_MODEL_PATH)
            if not path.is_file():
                raise StickerError("STICKER_MODEL_NOT_CONFIGURED")
            if hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
                raise StickerError("STICKER_MODEL_INVALID")
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.enable_cpu_mem_arena = False
            options.enable_mem_pattern = False
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
            options.inter_op_num_threads = 1
            options.log_severity_level = 3
            _session = ort.InferenceSession(
                str(path), sess_options=options, providers=["CPUExecutionProvider"]
            )
        rgb = image.convert("RGB")
        rgb.thumbnail((768, 768), Image.Resampling.LANCZOS)
        sample = np.asarray(rgb.resize((320, 320), Image.Resampling.LANCZOS), dtype=np.float32)
        sample /= max(float(sample.max()), 1.0)
        sample = (sample - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
            [0.229, 0.224, 0.225], dtype=np.float32
        )
        tensor = sample.transpose((2, 0, 1))[None, ...]
        values = _session.run(None, {_session.get_inputs()[0].name: tensor})[0][0, 0]
        low, high = float(values.min()), float(values.max())
        if high - low < 1e-6:
            raise StickerError("STICKER_OBJECT_NOT_CLEAR")
        alpha = np.clip((values - low) * (255 / (high - low)), 0, 255).astype(np.uint8)
        mask = Image.fromarray(alpha).resize(rgb.size, Image.Resampling.LANCZOS)
        mask = mask.point(lambda value: 0 if value < 24 else 255 if value > 232 else value)
        bounds = mask.point(lambda value: 255 if value > 100 else 0).getbbox()
        if bounds is None:
            raise StickerError("STICKER_OBJECT_NOT_CLEAR")
        rgba = rgb.convert("RGBA")
        rgba.putalpha(mask)
        rgba = rgba.crop(bounds)
        rgba = ImageOps.expand(rgba, border=18, fill=(0, 0, 0, 0))
        outline = Image.new("RGBA", rgba.size, "white")
        outline.putalpha(rgba.getchannel("A").filter(ImageFilter.MaxFilter(17)))
        return Image.alpha_composite(outline, rgba)
