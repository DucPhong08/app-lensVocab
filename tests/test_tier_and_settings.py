from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from app.dependencies.auth import get_current_user
from app.main import app
from app.models.models import AccountTier, User, UserPreferences
from app.services.quota_service import MaintenanceModeError, check_and_consume_quota
from app.services.system_setting_service import update_system_settings
from app.services.tier_service import TierPolicyViolation, validate_user_preferences


class TestTierAndSettings(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.free_user = User.model_construct(
            id=uuid.uuid4(),
            email="free_user@example.com",
            hashed_password="pw",
            display_name="Free User",
            account_tier=AccountTier.FREE,
            daily_quota_left=10,
            is_active=True,
            preferences=UserPreferences(),
        )
        self.premium_user = User.model_construct(
            id=uuid.uuid4(),
            email="premium_user@example.com",
            hashed_password="pw",
            display_name="Premium User",
            account_tier=AccountTier.PREMIUM,
            daily_quota_left=200,
            is_active=True,
            preferences=UserPreferences(),
        )
        self.save_patcher = patch.object(User, "save", new_callable=AsyncMock)
        self.mock_save = self.save_patcher.start()

    def tearDown(self):
        self.save_patcher.stop()
        app.dependency_overrides.clear()

    # ── 1. Unit Test Tier Policy Logic ───────────────────────────────────────
    def test_tier_policy_validation_free_tier(self):
        # Hợp lệ với Free
        valid_pref = UserPreferences(
            preferred_voice_id="Joanna", voice_speed=1.0, daily_review_goal=15
        )
        validate_user_preferences(AccountTier.FREE, valid_pref)

        # Vi phạm giọng đọc
        invalid_voice = UserPreferences(preferred_voice_id="Matthew", voice_speed=1.0)
        with self.assertRaises(TierPolicyViolation):
            validate_user_preferences(AccountTier.FREE, invalid_voice)

        # Vi phạm tốc độ đọc
        invalid_speed = UserPreferences(preferred_voice_id="Joanna", voice_speed=1.25)
        with self.assertRaises(TierPolicyViolation):
            validate_user_preferences(AccountTier.FREE, invalid_speed)

        # Vi phạm review goal
        excessive_goal = UserPreferences(
            preferred_voice_id="Joanna", voice_speed=1.0, daily_review_goal=50
        )
        with self.assertRaises(TierPolicyViolation):
            validate_user_preferences(AccountTier.FREE, excessive_goal)

    def test_tier_policy_validation_premium_tier(self):
        # Premium được phép dùng nhiều giọng và tốc độ
        premium_pref = UserPreferences(
            preferred_voice_id="Amy", voice_speed=0.75, daily_review_goal=50
        )
        validate_user_preferences(AccountTier.PREMIUM, premium_pref)

        premium_pref_2 = UserPreferences(
            preferred_voice_id="Brian", voice_speed=1.25, daily_review_goal=100
        )
        validate_user_preferences(AccountTier.PREMIUM, premium_pref_2)

    # ── 2. Test User Preferences API ─────────────────────────────────────────
    def test_get_preferences_endpoint(self):
        async def override_user():
            return self.free_user

        app.dependency_overrides[get_current_user] = override_user
        res = self.client.get("/users/me/preferences", headers={"Authorization": "Bearer token"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["account_tier"], "FREE")
        self.assertEqual(data["allowed_voices"], ["Joanna"])
        self.assertFalse(data["allow_neural_voice"])
        self.assertEqual(data["preferences"]["preferred_voice_id"], "Joanna")

    def test_patch_preferences_free_tier_forbidden_voice(self):
        async def override_user():
            return self.free_user

        app.dependency_overrides[get_current_user] = override_user
        res = self.client.patch(
            "/users/me/preferences",
            json={"preferred_voice_id": "Matthew"},
            headers={"Authorization": "Bearer token"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("không thuộc quyền lợi gói FREE", res.json()["detail"])

    def test_patch_preferences_premium_tier_success(self):
        async def override_user():
            return self.premium_user

        app.dependency_overrides[get_current_user] = override_user
        res = self.client.patch(
            "/users/me/preferences",
            json={"preferred_voice_id": "Matthew", "voice_speed": 1.25, "daily_review_goal": 30},
            headers={"Authorization": "Bearer token"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["preferences"]["preferred_voice_id"], "Matthew")
        self.assertEqual(data["preferences"]["voice_speed"], 1.25)
        self.assertEqual(data["preferences"]["daily_review_goal"], 30)

    # ── 3. Test Admin System Settings API ────────────────────────────────────
    def test_get_and_patch_admin_settings(self):
        async def override_user():
            return self.premium_user

        app.dependency_overrides[get_current_user] = override_user

        # GET admin settings
        res_get = self.client.get("/admin/settings", headers={"Authorization": "Bearer token"})
        self.assertEqual(res_get.status_code, 200)
        settings_data = res_get.json()
        self.assertIn("free_daily_quota", settings_data)
        self.assertIn("maintenance_mode", settings_data)
        self.assertIn("max_detected_objects", settings_data)

        # PATCH admin settings (cập nhật trần phát hiện vật thể)
        res_patch = self.client.patch(
            "/admin/settings",
            json={"free_daily_quota": 25, "maintenance_mode": False, "max_detected_objects": 6},
            headers={"Authorization": "Bearer token"},
        )
        self.assertEqual(res_patch.status_code, 200)
        self.assertEqual(res_patch.json()["free_daily_quota"], 25)
        self.assertEqual(res_patch.json()["max_detected_objects"], 6)

    def test_patch_preferences_max_detected_objects(self):
        async def override_user():
            return self.free_user

        app.dependency_overrides[get_current_user] = override_user
        res = self.client.patch(
            "/users/me/preferences",
            json={"max_detected_objects": 7},
            headers={"Authorization": "Bearer token"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["preferences"]["max_detected_objects"], 7)

    def test_patch_preferences_invalid_max_detected_objects(self):
        async def override_user():
            return self.free_user

        app.dependency_overrides[get_current_user] = override_user
        # Quá giới hạn le=10
        res = self.client.patch(
            "/users/me/preferences",
            json={"max_detected_objects": 15},
            headers={"Authorization": "Bearer token"},
        )
        self.assertEqual(res.status_code, 422)

    # ── 4. Test Maintenance Mode In Quota Service ────────────────────────────
    async def test_quota_service_maintenance_mode(self):
        mock_redis = AsyncMock()
        await update_system_settings({"maintenance_mode": True})

        with self.assertRaises(MaintenanceModeError):
            await check_and_consume_quota(self.free_user, mock_redis)

        # Khôi phục maintenance_mode về False
        await update_system_settings({"maintenance_mode": False})


if __name__ == "__main__":
    unittest.main()
