from __future__ import annotations

import io
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from botocore.exceptions import ClientError
from starlette.testclient import TestClient

from app.dependencies.auth import get_current_user
from app.main import app
from app.models.models import AccountTier, User, UserPreferences
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
            "/api/v1/scan", files=files, headers={"Authorization": "Bearer token"}
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "INVALID_IMAGE_FORMAT")

    def test_empty_image_returns_400(self):
        files = {"file": ("empty.jpg", io.BytesIO(b""), "image/jpeg")}
        res = self.client.post(
            "/api/v1/scan", files=files, headers={"Authorization": "Bearer token"}
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "FILE_EMPTY")

    def test_image_larger_than_5mb_returns_413(self):
        large_bytes = b"0" * (5 * 1024 * 1024 + 1)
        files = {"file": ("large.jpg", io.BytesIO(large_bytes), "image/jpeg")}
        res = self.client.post(
            "/api/v1/scan", files=files, headers={"Authorization": "Bearer token"}
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
            "/api/v1/scan", files=files, headers={"Authorization": "Bearer token"}
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
            "/api/v1/scan", files=files, headers={"Authorization": "Bearer token"}
        )
        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.json()["detail"], "AWS_VISION_UNAVAILABLE")

    # ── 3. High Confidence Success Scan ──────────────────────────────────────
    @patch("app.routers.vision.resolve_flashcard", new_callable=AsyncMock)
    @patch("app.routers.vision.detect_image_labels", new_callable=AsyncMock)
    def test_high_confidence_scan_success(self, mock_detect, mock_resolve):
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
        mock_resolve.return_value = FlashcardPayload(
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
            "/api/v1/scan", files=files, headers={"Authorization": "Bearer token"}
        )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "OK")
        self.assertEqual(data["keyword"], "chair")
        self.assertEqual(data["meaning_vi"], "cái ghế")
        self.assertEqual(data["confidence"], 95.0)
        self.assertEqual(data["bounding_box"]["width"], 0.5)

    # ── 4. Low Confidence Graceful Fallback ───────────────────────────────────
    @patch("app.routers.vision.resolve_flashcard", new_callable=AsyncMock)
    @patch("app.services.degradation_service.fallback_keyword_from_context", new_callable=AsyncMock)
    @patch("app.routers.vision.detect_image_labels", new_callable=AsyncMock)
    def test_low_confidence_fallback_scan(self, mock_detect, mock_fallback, mock_resolve):
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
        mock_resolve.return_value = FlashcardPayload(
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
            "/api/v1/scan", files=files, headers={"Authorization": "Bearer token"}
        )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "FALLBACK")
        self.assertEqual(data["keyword"], "desk")
        self.assertEqual(data["meaning_vi"], "bàn làm việc")


if __name__ == "__main__":
    unittest.main()
