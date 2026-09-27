from __future__ import annotations

import io
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from botocore.exceptions import ClientError
from starlette.testclient import TestClient

from app.dependencies.auth import get_current_user
from app.main import app
from app.models.user import AccountTier, User, UserPreferences
from app.services.ai_service import BoundingBox, RekognitionLabel, VisionResult
from app.services.cache_service import FlashcardPayload


class TestVisionPipeline(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.mock_user = User.model_construct(
            id=uuid.uuid4(),
            email="test_vision_user@example.com",
            hashed_password="pw",
            display_name="Vision User",
            account_tier=AccountTier.FREE,
            daily_quota_left=10,
            is_active=True,
            preferences=UserPreferences(),
        )

        async def override_user():
            return self.mock_user

        app.dependency_overrides[get_current_user] = override_user

        # Mock quota check để test tập trung vào vision pipeline
        self.quota_patcher = patch(
            "app.routers.vision.check_and_consume_quota",
            new_callable=AsyncMock,
        )
        self.mock_quota = self.quota_patcher.start()

    def tearDown(self):
        self.quota_patcher.stop()
        app.dependency_overrides.clear()

    # ── 1. Input Validation Tests ────────────────────────────────────────────
    def test_invalid_content_type_returns_400(self):
        file_content = b"fake pdf content"
        files = {"file": ("test.pdf", io.BytesIO(file_content), "application/pdf")}
        res = self.client.post(
            "/vision/scan", files=files, headers={"Authorization": "Bearer token"}
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "INVALID_IMAGE_FORMAT")

    def test_empty_image_returns_400(self):
        files = {"file": ("empty.jpg", io.BytesIO(b""), "image/jpeg")}
        res = self.client.post(
            "/vision/scan", files=files, headers={"Authorization": "Bearer token"}
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "FILE_EMPTY")

    def test_image_larger_than_5mb_returns_413(self):
        large_bytes = b"0" * (5 * 1024 * 1024 + 1)
        files = {"file": ("large.jpg", io.BytesIO(large_bytes), "image/jpeg")}
        res = self.client.post(
            "/vision/scan", files=files, headers={"Authorization": "Bearer token"}
        )
        self.assertEqual(res.status_code, 413)
        self.assertEqual(res.json()["detail"], "IMAGE_TOO_LARGE")

    # ── 2. AWS Error Handling Tests ──────────────────────────────────────────
    @patch("app.routers.vision.detect_image_labels", new_callable=AsyncMock)
    def test_aws_invalid_image_data_error_returns_400(self, mock_detect):
        mock_detect.side_effect = ClientError(
            {"Error": {"Code": "InvalidImageFormatException", "Message": "Bad image"}},
            "DetectLabels",
        )
        files = {"file": ("corrupt.jpg", io.BytesIO(b"corrupt bytes"), "image/jpeg")}
        res = self.client.post(
            "/vision/scan", files=files, headers={"Authorization": "Bearer token"}
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "INVALID_IMAGE_DATA")

    @patch("app.routers.vision.detect_image_labels", new_callable=AsyncMock)
    def test_aws_service_unavailable_error_returns_502(self, mock_detect):
        mock_detect.side_effect = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "DetectLabels",
        )
        files = {"file": ("test.jpg", io.BytesIO(b"valid image bytes"), "image/jpeg")}
        res = self.client.post(
            "/vision/scan", files=files, headers={"Authorization": "Bearer token"}
        )
        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.json()["detail"], "AWS_VISION_UNAVAILABLE")

    # ── 3. High Confidence Success Scan ──────────────────────────────────────
    @patch("app.routers.vision.get_flashcard", new_callable=AsyncMock)
    @patch("app.routers.vision.detect_image_labels", new_callable=AsyncMock)
    def test_high_confidence_scan_success(self, mock_detect, mock_get_flashcard):
        mock_detect.return_value = VisionResult(
            labels=[RekognitionLabel("Chair", 95.0, ["Furniture"], ["Seat"], [])],
            top_label="Chair",
            top_confidence=95.0,
            top_bounding_box=BoundingBox(0.5, 0.5, 0.1, 0.1),
            top_categories=["Furniture"],
            top_aliases=["Seat"],
            top_parents=[],
            raw_description="a wooden chair in a room",
        )
        mock_get_flashcard.return_value = FlashcardPayload(
            keyword="chair",
            pronunciation="/tʃer/",
            meaning_vi="cái ghế",
            example_1="I sit on a chair.",
            example_2="A wooden chair.",
            related_words=["seat", "stool"],
            audio_base64="mp3_mock_data",
            source="redis",
            is_draft=False,
        )

        files = {"file": ("chair.jpg", io.BytesIO(b"valid image data"), "image/jpeg")}
        res = self.client.post(
            "/vision/scan", files=files, headers={"Authorization": "Bearer token"}
        )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "OK")
        self.assertEqual(data["keyword"], "chair")
        self.assertEqual(data["meaning_vi"], "cái ghế")
        self.assertEqual(data["confidence"], 95.0)
        self.assertEqual(data["bounding_box"]["width"], 0.5)
        # Kiểm tra candidate_keywords được truyền đúng: top_parents + top_aliases
        mock_get_flashcard.assert_called_once()
        call_kwargs = mock_get_flashcard.call_args.kwargs
        self.assertIn("candidate_keywords", call_kwargs)
        self.assertEqual(sorted(call_kwargs["candidate_keywords"]), sorted(["Seat"]))

    # ── 3b. Parents/Aliases Cache Hit ────────────────────────────────────────────
    @patch("app.routers.vision.get_flashcard", new_callable=AsyncMock)
    @patch("app.routers.vision.detect_image_labels", new_callable=AsyncMock)
    def test_parent_alias_passed_as_candidates(self, mock_detect, mock_get_flashcard):
        """Khi Rekognition trả về nhãn 'Armchair' với parent 'Chair',
        router phải truyền ['Chair'] vào candidate_keywords để cache resolver
        có thể tìm thời exact match trước khi gọi Bedrock.
        """
        mock_detect.return_value = VisionResult(
            labels=[RekognitionLabel("Armchair", 88.0, ["Furniture"], [], ["Chair"])],
            top_label="Armchair",
            top_confidence=88.0,
            top_bounding_box=BoundingBox(0.4, 0.6, 0.2, 0.1),
            top_categories=["Furniture"],
            top_aliases=[],
            top_parents=["Chair"],
            raw_description="a green armchair near window",
        )
        mock_get_flashcard.return_value = FlashcardPayload(
            keyword="chair",
            pronunciation="/tʃer/",
            meaning_vi="cái ghế",
            example_1="I sit on a chair.",
            example_2="A wooden chair.",
            related_words=["seat", "stool"],
            audio_base64="mp3_mock_data",
            source="mongodb",  # hit từ alias lookup
            is_draft=False,
        )

        files = {"file": ("armchair.jpg", io.BytesIO(b"valid image data"), "image/jpeg")}
        res = self.client.post(
            "/vision/scan", files=files, headers={"Authorization": "Bearer token"}
        )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "OK")
        # Kiểm tra candidate_keywords bao gồm đúng parent 'Chair'
        call_kwargs = mock_get_flashcard.call_args.kwargs
        self.assertIn("candidate_keywords", call_kwargs)
        self.assertIn("Chair", call_kwargs["candidate_keywords"])

    # ── 4. Low Confidence Graceful Fallback ───────────────────────────────────
    @patch("app.routers.vision.get_flashcard", new_callable=AsyncMock)
    @patch("app.services.degradation_service.fallback_keyword_from_context", new_callable=AsyncMock)
    @patch("app.routers.vision.detect_image_labels", new_callable=AsyncMock)
    def test_low_confidence_fallback_scan(self, mock_detect, mock_fallback, mock_get_flashcard):
        # Confidence < 50% -> triggers Bedrock fallback
        mock_detect.return_value = VisionResult(
            labels=[RekognitionLabel("Wood", 42.0, [], [], [])],
            top_label="Wood",
            top_confidence=42.0,
            top_bounding_box=None,
            top_categories=[],
            top_aliases=[],
            top_parents=[],
            raw_description="a wooden desk in office",
        )
        mock_fallback.return_value = "desk"
        mock_get_flashcard.return_value = FlashcardPayload(
            keyword="desk",
            pronunciation="/desk/",
            meaning_vi="bàn làm việc",
            example_1="Work at the desk.",
            example_2="My office desk.",
            related_words=["table"],
            audio_base64="mp3_mock_data",
            source="bedrock",
            is_draft=True,
        )

        files = {"file": ("desk.jpg", io.BytesIO(b"valid image data"), "image/jpeg")}
        res = self.client.post(
            "/vision/scan", files=files, headers={"Authorization": "Bearer token"}
        )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "FALLBACK")
        self.assertEqual(data["keyword"], "desk")
        self.assertEqual(data["meaning_vi"], "bàn làm việc")

    # ── 5. SSE Progressive Streaming Tests ───────────────────────────────────
    def test_scan_stream_empty_file_returns_400(self):
        files = {"file": ("empty.jpg", io.BytesIO(b""), "image/jpeg")}
        res = self.client.post(
            "/vision/scan/stream", files=files, headers={"Authorization": "Bearer token"}
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "FILE_EMPTY")

    @patch("app.routers.vision.stream_flashcard")
    @patch("app.routers.vision.detect_image_labels", new_callable=AsyncMock)
    def test_scan_stream_success(self, mock_detect, mock_stream_flashcard):
        mock_detect.return_value = VisionResult(
            labels=[RekognitionLabel("Chair", 95.0, ["Furniture"], ["Seat"], [])],
            top_label="Chair",
            top_confidence=95.0,
            top_bounding_box=BoundingBox(0.5, 0.5, 0.1, 0.1),
            top_categories=["Furniture"],
            top_aliases=["Seat"],
            top_parents=[],
            raw_description="a wooden chair in a room",
        )

        async def fake_stream(keyword, redis, **kwargs):
            yield {
                "event": "vocab_content",
                "data": {
                    "step": "VOCAB_CONTENT",
                    "source": "redis",
                    "keyword": "chair",
                    "pronunciation": "/tʃer/",
                    "meaning_vi": "cái ghế",
                },
            }
            yield {
                "event": "audio_ready",
                "data": {"step": "AUDIO_READY", "audio_base64": "mp3_data"},
            }
            yield {"event": "done", "data": {"step": "DONE", "status": "SUCCESS"}}

        mock_stream_flashcard.side_effect = fake_stream

        files = {"file": ("chair.jpg", io.BytesIO(b"valid image data"), "image/jpeg")}
        res = self.client.post(
            "/vision/scan/stream", files=files, headers={"Authorization": "Bearer token"}
        )

        self.assertEqual(res.status_code, 200)
        self.assertIn("text/event-stream", res.headers.get("content-type", ""))
        self.assertIn("event: vision_detected", res.text)
        self.assertIn("event: vocab_content", res.text)
        self.assertIn("event: audio_ready", res.text)
        self.assertIn("event: done", res.text)
        self.assertIn('"cái ghế"', res.text)

    # ── 6. Test Multi-object Detection & Admin Ceiling ────────────────────────
    @patch("app.routers.vision.fetch_system_settings", new_callable=AsyncMock)
    @patch("app.routers.vision.get_flashcard", new_callable=AsyncMock)
    @patch("app.routers.vision.detect_image_labels", new_callable=AsyncMock)
    def test_max_detected_objects_capped_by_admin_ceiling(
        self, mock_detect, mock_get_flashcard, mock_get_sys_settings
    ):
        """User cài đặt 7 nhưng Admin đặt trần 5 -> Không được vượt quá 5."""
        from app.models.setting import SystemSetting

        mock_get_sys_settings.return_value = SystemSetting.model_construct(max_detected_objects=5)
        self.mock_user.preferences.max_detected_objects = 7

        labels = [
            RekognitionLabel(
                name=f"Obj{i}",
                confidence=90.0 - i,
                categories=["General"],
                aliases=[],
                parents=[],
            )
            for i in range(1, 9)
        ]
        mock_detect.return_value = VisionResult(
            labels=labels[:5],
            top_label="Obj1",
            top_confidence=89.0,
            top_bounding_box=BoundingBox(0.2, 0.2, 0.1, 0.1),
            top_categories=["General"],
            top_aliases=[],
            top_parents=[],
            raw_description="multiple objects",
        )
        mock_get_flashcard.return_value = FlashcardPayload(
            keyword="obj1",
            pronunciation="/obj1/",
            meaning_vi="vật thể 1",
            example_1="Example 1",
            example_2="Example 2",
            related_words=[],
            audio_base64=None,
            source="bedrock",
            is_draft=False,
        )

        files = {"file": ("room.jpg", io.BytesIO(b"image bytes"), "image/jpeg")}
        res = self.client.post(
            "/vision/scan", files=files, headers={"Authorization": "Bearer token"}
        )
        self.assertEqual(res.status_code, 200)

        # Xác nhận detect_image_labels bị chốt cứng ở 5 vì trần của admin
        mock_detect.assert_called_once_with(b"image bytes", max_labels=5)
        data = res.json()
        self.assertEqual(len(data["detected_objects"]), 5)
        self.assertEqual(data["detected_objects"][0]["keyword"], "Obj1")

    @patch("app.routers.vision.fetch_system_settings", new_callable=AsyncMock)
    @patch("app.routers.vision.get_flashcard", new_callable=AsyncMock)
    @patch("app.routers.vision.detect_image_labels", new_callable=AsyncMock)
    def test_max_detected_objects_user_lower_than_admin(
        self, mock_detect, mock_get_flashcard, mock_get_sys_settings
    ):
        """User cài đặt 3 trong khi Admin cho phép 5 -> Lấy theo 3 của User."""
        from app.models.setting import SystemSetting

        mock_get_sys_settings.return_value = SystemSetting.model_construct(max_detected_objects=5)
        self.mock_user.preferences.max_detected_objects = 3

        labels = [
            RekognitionLabel(
                name=f"Obj{i}",
                confidence=90.0 - i,
                categories=["General"],
                aliases=[],
                parents=[],
            )
            for i in range(1, 4)
        ]
        mock_detect.return_value = VisionResult(
            labels=labels,
            top_label="Obj1",
            top_confidence=89.0,
            top_bounding_box=None,
            top_categories=["General"],
            top_aliases=[],
            top_parents=[],
            raw_description="3 objects",
        )
        mock_get_flashcard.return_value = FlashcardPayload(
            keyword="obj1",
            pronunciation="/obj1/",
            meaning_vi="vật thể 1",
            example_1="Example 1",
            example_2="Example 2",
            related_words=[],
            audio_base64=None,
            source="bedrock",
            is_draft=False,
        )

        files = {"file": ("room.jpg", io.BytesIO(b"image bytes"), "image/jpeg")}
        res = self.client.post(
            "/vision/scan", files=files, headers={"Authorization": "Bearer token"}
        )
        self.assertEqual(res.status_code, 200)

        # Xác nhận detect_image_labels được gọi với 3 theo lựa chọn của user
        mock_detect.assert_called_once_with(b"image bytes", max_labels=3)
        data = res.json()
        self.assertEqual(len(data["detected_objects"]), 3)


if __name__ == "__main__":
    unittest.main()
