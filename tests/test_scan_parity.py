import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.setting import SystemSetting
from app.models.user import User
from app.services import cache_service as cache
from app.services import vision_service as vision
from app.services.ai_service import BoundingBox, RekognitionLabel, VisionResult
from app.services.degradation_service import DegradationResult, VisionAnalysis


class ScanParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_and_stream_share_detection_fields_for_all_outcomes(self):
        box = BoundingBox(0.3, 0.4, 0.1, 0.2)
        result = VisionResult(
            [RekognitionLabel("Cup", 99, ["Kitchen"], ["Mug"], ["Container"], box)],
            "Cup",
            99,
            box,
            ["Kitchen"],
            ["Mug"],
            ["Container"],
            "a cup",
        )
        payload = cache.FlashcardPayload("cup", None, "cốc", "A", "B", [], None, "mongodb", False)
        user = User.model_construct()
        system = SystemSetting.model_construct()
        for status in DegradationResult:
            keyword = None if status == DegradationResult.UNRECOGNIZABLE else "cup"
            analysis = VisionAnalysis(
                status=status, keyword=keyword, confidence=99, message="fixture"
            )

            async def vocabulary(*args, **kwargs):
                yield {"event": "done", "data": {"status": "SUCCESS"}}

            with (
                self.subTest(status=status),
                patch.object(vision, "detect_image_labels", AsyncMock(return_value=result)),
                patch.object(vision, "evaluate_vision_result", AsyncMock(return_value=analysis)),
                patch.object(vision, "get_flashcard", AsyncMock(return_value=payload)) as lookup,
                patch.object(vision, "stream_flashcard", side_effect=vocabulary),
            ):
                http = await vision.execute_vision_scan(
                    b"fixture", user, object(), system_settings=system
                )
                events = [
                    item
                    async for item in vision.execute_vision_stream(
                        b"fixture", user, object(), system_settings=system
                    )
                ]
                detected = next(
                    json.loads(item.split("data: ", 1)[1])
                    for item in events
                    if item.startswith("event: vision_detected")
                )
                for field in [
                    "status",
                    "keyword",
                    "confidence",
                    "bounding_box",
                    "categories",
                    "aliases",
                    "parents",
                    "message",
                ]:
                    self.assertEqual(http.model_dump()[field], detected[field], field)
                if status == DegradationResult.OK:
                    self.assertIn(
                        http.selected_object_id, [item.object_id for item in http.detected_objects]
                    )
                    self.assertIn(
                        detected["selected_object_id"],
                        [item["object_id"] for item in detected["detected_objects"]],
                    )
                else:
                    self.assertIsNone(http.selected_object_id)
                    self.assertIsNone(detected["selected_object_id"])
                if status == DegradationResult.UNRECOGNIZABLE:
                    lookup.assert_not_awaited()

    async def test_cached_content_fields_match_between_http_and_stream(self):
        for source in ["redis", "mongodb"]:
            for audio in [None, "fixture-mp3"]:
                payload = cache.FlashcardPayload(
                    "cup", None, "cốc", "A", "B", [], audio, source, source == "redis"
                )
                with (
                    self.subTest(source=source, audio=bool(audio)),
                    patch.object(
                        cache,
                        "_get_from_redis",
                        AsyncMock(return_value=payload if source == "redis" else None),
                    ),
                    patch.object(
                        cache,
                        "_find_exact_mongodb",
                        AsyncMock(return_value=SimpleNamespace(**payload.to_dict())),
                    ),
                    patch.object(cache, "_save_to_redis", AsyncMock()),
                    patch.object(cache, "generate_flashcard_content", AsyncMock()) as generate,
                ):
                    http = await cache.get_flashcard(" CUP ", object())
                    events = [item async for item in cache.stream_flashcard(" CUP ", object())]
                    content = next(
                        item["data"] for item in events if item["event"] == "vocab_content"
                    )
                    expected = http.to_dict()
                    expected.pop("audio_base64")
                    self.assertEqual(content, {"step": "VOCAB_CONTENT", **expected})
                    self.assertEqual(
                        any(item["event"] == "audio_ready" for item in events), bool(audio)
                    )
                    self.assertEqual(events[-1]["data"]["source"], source)
                    generate.assert_not_awaited()
