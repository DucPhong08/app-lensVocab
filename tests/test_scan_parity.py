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


async def scan_both(user, settings):
    redis = object()
    http = await vision.execute_vision_scan(b"fixture", user, redis, system_settings=settings)
    events = [
        frame
        async for frame in vision.execute_vision_stream(
            b"fixture", user, redis, system_settings=settings
        )
    ]
    detected = next(frame for frame in events if frame.startswith("event: vision_detected"))
    return http, json.loads(detected.split("data: ", 1)[1])


class ScanParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_and_selectable_objects_follow_the_same_scan_threshold(self):
        cup = RekognitionLabel("Cup", 55, [], [], [], BoundingBox(0.2, 0.3, 0.1, 0.2))
        book = RekognitionLabel("Book", 45, [], [], [], BoundingBox(0.2, 0.3, 0.6, 0.2))
        result = VisionResult([cup, book], "Cup", 55, cup.bounding_box, [], [], [], "")
        user = User.model_construct()
        payload = cache.FlashcardPayload("cup", None, "cốc", "A", "B", [], None, "redis", False)
        with (
            patch.object(vision, "detect_image_labels", AsyncMock(return_value=result)),
            patch.object(cache, "_get_from_redis", AsyncMock(return_value=payload)),
        ):
            for threshold, selectable in [(90, []), (0.55, ["Cup"]), (0.45, ["Cup", "Book"])]:
                with self.subTest(threshold=threshold):
                    settings = SystemSetting.model_construct(vision_confidence_threshold=threshold)
                    http, streamed = await scan_both(user, settings)
                    for scan in (http.model_dump(), streamed):
                        self.assertEqual(scan["keyword"], "cup" if selectable else None)
                        self.assertEqual(
                            [
                                item["keyword"]
                                for item in scan["detected_objects"]
                                if item["selection_token"]
                            ],
                            selectable,
                        )

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
                http, detected = await scan_both(user, system)
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
