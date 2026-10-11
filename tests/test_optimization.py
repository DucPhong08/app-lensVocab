import json
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from pydantic import ValidationError
from pymongo.errors import AutoReconnect

from app.dependencies.auth import get_current_user
from app.main import app
from app.models.setting import SystemSetting
from app.models.user import AccountTier, User
from app.schemas.flashcard import ConfirmFlashcardRequest
from app.services import ai_service, cache_service, system_setting_service


class OptimizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_admin_cannot_read_or_update_settings(self):
        for tier in AccountTier:

            async def user():
                return User.model_construct(id=uuid.uuid4(), account_tier=tier)

            app.dependency_overrides[get_current_user] = user
            try:
                with patch(
                    "app.routers.admin.update_system_settings", new_callable=AsyncMock
                ) as update:
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        response = await client.get("/admin/settings")
                        self.assertEqual(response.status_code, 403)
                        response = await client.patch(
                            "/admin/settings", json={"maintenance_mode": True}
                        )
                        self.assertEqual(response.status_code, 403)
                        update.assert_not_awaited()
            finally:
                app.dependency_overrides.clear()

    async def test_failed_setting_write_preserves_cached_value(self):
        original = SystemSetting.model_construct(id="global_config", maintenance_mode=False)
        with (
            patch.object(system_setting_service, "_cached_setting", original),
            patch.object(
                system_setting_service, "fetch_system_settings", AsyncMock(return_value=original)
            ),
            patch.object(SystemSetting, "save", AsyncMock(side_effect=AutoReconnect("offline"))),
        ):
            with self.assertRaises(Exception):
                await system_setting_service.update_system_settings({"maintenance_mode": True})
            self.assertFalse(original.maintenance_mode)
            self.assertFalse(system_setting_service._cached_setting.maintenance_mode)

    async def test_parent_cache_never_replaces_exact_keyword(self):
        parent = cache_service.FlashcardPayload(
            "furniture", None, "đồ nội thất", "A.", "B.", [], None, "mongodb", False
        )

        async def lookup(redis, keyword):
            return parent if keyword == "furniture" else None

        exact = MagicMock(
            keyword="chair",
            pronunciation=None,
            meaning_vi="ghế",
            example_1="A.",
            example_2="B.",
            related_words=[],
            audio_base64=None,
        )
        with (
            patch.object(cache_service, "_get_from_redis", side_effect=lookup),
            patch.object(cache_service, "_find_exact_mongodb", AsyncMock(return_value=exact)),
            patch.object(cache_service, "_save_to_redis", AsyncMock()),
        ):
            result = await cache_service.get_flashcard("chair", AsyncMock(), ["furniture"])
            self.assertEqual(result.keyword, "chair")

    async def test_redis_hit_reports_current_source(self):
        payload = cache_service.FlashcardPayload(
            "chair", None, "ghế", "A.", "B.", [], None, "bedrock", True
        )
        redis = AsyncMock()
        redis.get.return_value = json.dumps(payload.to_dict())
        result = await cache_service.get_flashcard("chair", redis)
        self.assertEqual(result.source, "redis")

    def test_invalid_ai_json_shape_is_retryable(self):
        for output in ("{}", "[]", "null", '{"meaning_vi": 123}'):
            client = MagicMock()
            client.converse.return_value = {"output": {"message": {"content": [{"text": output}]}}}
            with (
                self.subTest(output=output),
                patch.object(ai_service, "_get_bedrock_client", return_value=client),
            ):
                with self.assertRaises(ai_service.BedrockOutputError):
                    ai_service._sync_generate_content.__wrapped__("chair")

    def test_confirm_keyword_normalization_and_only_keyword_required(self):
        self.assertEqual(ConfirmFlashcardRequest(keyword=" Chair ").keyword, "chair")
        for word in (" ", "x" * 101):
            with self.assertRaises(ValidationError):
                ConfirmFlashcardRequest(keyword=word)

    async def test_admin_save_failure_returns_503(self):
        async def admin():
            return User.model_construct(id=uuid.uuid4(), is_admin=True)

        app.dependency_overrides[get_current_user] = admin
        try:
            with patch(
                "app.routers.admin.update_system_settings",
                AsyncMock(side_effect=AutoReconnect("offline")),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.patch(
                        "/admin/settings", json={"maintenance_mode": True}
                    )
                    self.assertEqual(response.status_code, 503)
        finally:
            app.dependency_overrides.clear()

    async def test_scan_ai_failure_is_502_and_stream_has_error_event(self):
        from app.services.ai_service import RekognitionLabel, VisionResult

        async def user():
            return User.model_construct(id=uuid.uuid4())

        app.dependency_overrides[get_current_user] = user
        detection = VisionResult(
            [RekognitionLabel("Chair", 99, [], [], [])], "Chair", 99, None, [], [], [], "Chair"
        )
        try:
            with (
                patch("app.routers.vision.check_and_consume_quota", AsyncMock()),
                patch(
                    "app.services.vision_service.detect_image_labels",
                    AsyncMock(return_value=detection),
                ),
                patch(
                    "app.services.vision_service.get_flashcard",
                    AsyncMock(side_effect=ai_service.BedrockOutputError("bad")),
                ),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    result = await client.post(
                        "/vision/scan", files={"file": ("photo.jpg", b"image", "image/jpeg")}
                    )
                    self.assertEqual(result.status_code, 502)
                    self.assertEqual(result.json()["detail"], "AI_CONTENT_INVALID")

            async def failed_stream(*args):
                raise ai_service.BedrockOutputError("bad")
                yield  # make it an async generator

            with (
                patch("app.routers.vision.check_and_consume_quota", AsyncMock()),
                patch("app.routers.vision.execute_vision_stream", failed_stream),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    result = await client.post(
                        "/vision/scan/stream", files={"file": ("photo.jpg", b"image", "image/jpeg")}
                    )
                    self.assertIn("event: error", result.text)
                    self.assertIn("AI_CONTENT_INVALID", result.text)
        finally:
            app.dependency_overrides.clear()

    async def test_live_vision_threshold_is_used(self):
        from app.services.degradation_service import DegradationResult, evaluate_vision_result

        settings = SystemSetting.model_construct(vision_confidence_threshold=0.9)
        with (
            patch(
                "app.services.degradation_service.fetch_system_settings",
                AsyncMock(return_value=settings),
            ),
            patch(
                "app.services.degradation_service.fallback_keyword_from_context",
                AsyncMock(return_value="desk"),
            ),
        ):
            result = await evaluate_vision_result("chair", 85, "office")
            self.assertEqual(result.status, DegradationResult.FALLBACK_CONTEXT)

    def test_aws_clients_are_reused_and_nova_uses_main_region(self):
        main, embedding = MagicMock(), MagicMock()
        with (
            patch.object(ai_service, "_clients", {}),
            patch.object(ai_service, "_get_boto3_session", return_value=main),
            patch.object(ai_service, "_get_embedding_session", return_value=embedding),
        ):
            first = ai_service._get_bedrock_client()
            self.assertIs(first, ai_service._get_bedrock_client())
            main.client.assert_called_once_with("bedrock-runtime", config=ai_service._BOTO_CONFIG)
            embedding.client.assert_not_called()
            ai_service._get_embedding_client()
            embedding.client.assert_called_once()
            ai_service.close_aws_clients()
            first.close.assert_called_once()

    async def test_null_settings_and_preferences_are_422_not_serialization_errors(self):
        async def admin():
            return User.model_construct(id=uuid.uuid4(), is_admin=True)

        app.dependency_overrides[get_current_user] = admin
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                for path, field in [
                    ("/admin/settings", "free_daily_quota"),
                    ("/users/me/preferences", "daily_review_goal"),
                ]:
                    response = await client.patch(path, json={field: None})
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json()["message"], "VALIDATION_ERROR")
        finally:
            app.dependency_overrides.clear()
