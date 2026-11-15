import unittest
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from app.dependencies.auth import get_current_user
from app.main import app
from app.models.setting import SystemSetting
from app.models.user import User, UserPreferences
from tests.preference_writes import mock_preference_writes


class LearningEffectsTests(unittest.TestCase):
    def setUp(self):
        self.user = User.model_construct(
            preferences=UserPreferences.model_validate({"daily_review_goal": 15})
        )
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.addCleanup(app.dependency_overrides.clear)
        system = patch(
            "app.services.user_service.fetch_system_settings",
            AsyncMock(return_value=SystemSetting.model_construct()),
        )
        system.start()
        self.addCleanup(system.stop)
        save = mock_preference_writes()
        self.save = save.start()
        self.addCleanup(save.stop)
        self.client = TestClient(app)

    def test_existing_accounts_default_to_enabled_and_each_option_can_be_saved_independently(self):
        before = self.client.get("/auth/me")
        self.assertEqual(before.status_code, 200)
        self.assertTrue(before.json()["preferences"]["animations_enabled"])
        self.assertTrue(before.json()["preferences"]["sound_effects_enabled"])
        first = self.client.patch("/users/me/preferences", json={"animations_enabled": False})
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["preferences"]["animations_enabled"])
        self.assertTrue(first.json()["preferences"]["sound_effects_enabled"])
        second = self.client.patch("/users/me/preferences", json={"sound_effects_enabled": False})
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["preferences"]["animations_enabled"])
        self.assertFalse(second.json()["preferences"]["sound_effects_enabled"])
        self.assertEqual(
            second.json()["preferences"]["voice_speed"], before.json()["preferences"]["voice_speed"]
        )
        self.assertEqual(self.save.call_count, 2)
        self.save.assert_called_with(self.user, {"preferences.sound_effects_enabled": False})

    def test_empty_patch_returns_current_preferences_without_a_write(self):
        response = self.client.patch("/users/me/preferences", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["preferences"], self.client.get("/auth/me").json()["preferences"]
        )
        self.save.assert_not_called()

    def test_effect_settings_require_json_booleans(self):
        for field in ("animations_enabled", "sound_effects_enabled"):
            for value in (None, "false", 0, [], {}):
                with self.subTest(field=field, value=value):
                    response = self.client.patch("/users/me/preferences", json={field: value})
                    self.assertEqual(response.status_code, 422)
        self.save.assert_not_called()
