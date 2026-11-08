import unittest
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from app.dependencies.auth import get_current_user
from app.main import app
from app.models.setting import SystemSetting
from app.models.user import User, UserPreferences
from app.services.scan_settings_service import scan_settings
from app.services.vision_service import resolve_max_objects


class ScanSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_and_offline_config_use_the_same_user_and_admin_limits(self):
        for requested, ceiling, expected in [(2, 10, 2), (10, 8, 8), (10, 10, 10), (7, 3, 3)]:
            with self.subTest(requested=requested, ceiling=ceiling):
                user = User.model_construct(
                    preferences=UserPreferences(max_detected_objects=requested)
                )
                settings = SystemSetting.model_construct(
                    max_detected_objects=ceiling, vision_confidence_threshold=0.72
                )
                result = scan_settings(user, settings)
                self.assertEqual(result.max_detected_objects, expected)
                self.assertEqual(result.max_allowed_objects, ceiling)
                self.assertEqual(result.confidence_threshold, 0.72)
                self.assertEqual(await resolve_max_objects(user, settings), expected)
                self.assertEqual(user.preferences.max_detected_objects, requested)

    async def test_guest_limit_and_legacy_threshold_remain_consistent(self):
        result = scan_settings(
            None,
            SystemSetting.model_construct(max_detected_objects=3, vision_confidence_threshold=65),
        )
        self.assertEqual(result.max_detected_objects, 3)
        self.assertEqual(result.confidence_threshold, 0.65)

    async def test_me_and_preferences_expose_only_the_safe_effective_scan_settings(self):
        user = User.model_construct(preferences=UserPreferences(max_detected_objects=10))
        settings = SystemSetting.model_construct(
            max_detected_objects=8, vision_confidence_threshold=0.7
        )
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            with (
                patch(
                    "app.services.user_service.fetch_system_settings",
                    AsyncMock(return_value=settings),
                ),
                patch.object(User, "set", AsyncMock()),
            ):
                client = TestClient(app)
                me = client.get("/auth/me")
                self.assertEqual(me.status_code, 200)
                data = me.json()
                self.assertEqual(
                    data["scan_settings"],
                    {
                        "max_detected_objects": 8,
                        "max_allowed_objects": 8,
                        "confidence_threshold": 0.7,
                    },
                )
                self.assertIn("allowed_speeds", data["preference_limits"])
                self.assertNotIn("maintenance_mode", data["scan_settings"])
                self.assertEqual(
                    client.get("/users/me/preferences").json()["scan_settings"],
                    data["scan_settings"],
                )
                changed = client.patch("/users/me/preferences", json={"max_detected_objects": 2})
                self.assertEqual(changed.status_code, 200)
                self.assertEqual(changed.json()["preferences"]["max_detected_objects"], 2)
                self.assertEqual(changed.json()["scan_settings"]["max_detected_objects"], 2)
                self.assertEqual(
                    client.patch(
                        "/users/me/preferences", json={"max_detected_objects": 11}
                    ).status_code,
                    422,
                )
        finally:
            app.dependency_overrides.clear()

    async def test_review_goal_in_settings_matches_the_queue_for_each_tier(self):
        from app.models.user import AccountTier
        from app.services.tier_service import review_goal
        from app.services.user_service import read_preferences

        settings = SystemSetting.model_construct(
            free_daily_review_cap=12, premium_daily_review_cap=25
        )
        for tier, goal, expected in [
            (AccountTier.FREE, 20, 12),
            (AccountTier.PREMIUM, 30, 25),
            (AccountTier.PREMIUM, 10, 10),
        ]:
            user = User.model_construct(
                account_tier=tier, preferences=UserPreferences(daily_review_goal=goal)
            )
            with patch(
                "app.services.user_service.fetch_system_settings", AsyncMock(return_value=settings)
            ):
                response = await read_preferences(user)
            self.assertEqual(response.effective_daily_review_goal, expected)
            self.assertEqual(review_goal(user, settings), expected)
