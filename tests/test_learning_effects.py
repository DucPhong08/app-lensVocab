import unittest
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from app.dependencies.auth import get_current_user
from app.main import app
from app.models.setting import SystemSetting
from app.models.user import User, UserPreferences


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
        save = patch.object(User, "set", AsyncMock())
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
        self.assertEqual(self.save.await_count, 2)
        self.assertFalse(self.save.call_args.args[0]["preferences"]["sound_effects_enabled"])

    def test_saved_effects_survive_user_document_reload(self):
        response = self.client.patch(
            "/users/me/preferences",
            json={"animations_enabled": False, "sound_effects_enabled": False},
        )
        self.assertEqual(response.status_code, 200)
        # Reconstruct from the exact preferences payload written to the document.
        persisted = self.save.call_args.args[0]["preferences"]
        self.user = User.model_construct(preferences=UserPreferences.model_validate(persisted))
        restored = self.client.get("/auth/me")
        self.assertEqual(restored.status_code, 200)
        self.assertFalse(restored.json()["preferences"]["animations_enabled"])
        self.assertFalse(restored.json()["preferences"]["sound_effects_enabled"])
        self.assertEqual(restored.json()["preferences"]["voice_speed"], 1.0)

    def test_effect_settings_require_json_booleans(self):
        for field in ("animations_enabled", "sound_effects_enabled"):
            for value in (None, "false", 0, [], {}):
                with self.subTest(field=field, value=value):
                    response = self.client.patch("/users/me/preferences", json={field: value})
                    self.assertEqual(response.status_code, 422)
        self.save.assert_not_awaited()
