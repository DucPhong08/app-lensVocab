import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.dependencies.auth import get_current_user
from app.main import app
from app.models.setting import SystemSetting
from app.models.user import User
from app.services.ai_service import RekognitionLabel, VisionResult
from app.services.cache_service import FlashcardPayload


class ScanSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_reads_one_settings_snapshot_and_next_request_sees_maintenance(self):
        current = SystemSetting.model_construct(maintenance_mode=False)
        user = User.model_construct()
        collection = MagicMock()
        collection.find_one_and_update = AsyncMock(return_value={"daily_quota_left": 9})
        detected = VisionResult(
            labels=[RekognitionLabel("Chair", 90, [], [], [])],
            top_label="Chair",
            top_confidence=90,
            top_bounding_box=None,
            top_categories=[],
            top_aliases=[],
            top_parents=[],
            raw_description="Chair",
        )
        payload = FlashcardPayload(
            "chair", None, "ghế", "A chair.", "My chair.", [], None, "mongodb", False
        )
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            with (
                patch.object(
                    SystemSetting, "get", AsyncMock(side_effect=lambda _: current)
                ) as read,
                patch.object(User, "get_motor_collection", return_value=collection),
                patch(
                    "app.routers.vision.get_redis", return_value=SimpleNamespace(ping=AsyncMock())
                ),
                patch(
                    "app.services.vision_service.detect_image_labels",
                    AsyncMock(return_value=detected),
                ) as detect,
                patch("app.services.vision_service.get_flashcard", AsyncMock(return_value=payload)),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    first = await client.post(
                        "/vision/scan", files={"file": ("a.jpg", b"offline", "image/jpeg")}
                    )
                    self.assertEqual(first.status_code, 200)
                    self.assertEqual(read.await_count, 1)
                    current = SystemSetting.model_construct(maintenance_mode=True)
                    second = await client.post(
                        "/vision/scan", files={"file": ("a.jpg", b"offline", "image/jpeg")}
                    )
                    self.assertEqual(second.status_code, 503)
                    self.assertEqual(second.json()["detail"], "MAINTENANCE_MODE")
                    self.assertEqual(read.await_count, 2)
                    detect.assert_awaited_once()
        finally:
            app.dependency_overrides.clear()
