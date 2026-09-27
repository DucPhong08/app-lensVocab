import unittest
import uuid
from starlette.testclient import TestClient

from app.main import app
from app.dependencies.auth import get_current_user
from app.models.models import AccountTier, User


class TestAPIAuthAndProtection(unittest.TestCase):
    def setUp(self):
        # We don't need database lifespan for routing/auth-header check
        self.client = TestClient(app, raise_server_exceptions=False)
        self.mock_user = User.model_construct(
            id=uuid.uuid4(),
            email="test_user@example.com",
            hashed_password="hashed_pw_example",
            display_name="Test User",
            account_tier=AccountTier.FREE,
            daily_quota_left=10,
            is_active=True,
        )

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_unauthenticated_endpoints_return_401(self):
        # 1. /api/v1/auth/me
        res_me = self.client.get("/api/v1/auth/me")
        self.assertEqual(res_me.status_code, 401)

        # 2. /api/v1/scan
        res_scan = self.client.post("/api/v1/scan")
        self.assertEqual(res_scan.status_code, 401)

        # 3. /api/v1/flashcards
        res_fc = self.client.get("/api/v1/flashcards")
        self.assertEqual(res_fc.status_code, 401)

        # 4. /api/v1/review/today
        res_rv = self.client.get("/api/v1/review/today")
        self.assertEqual(res_rv.status_code, 401)

    def test_authenticated_me_endpoint_with_override(self):
        async def override_get_current_user():
            return self.mock_user

        app.dependency_overrides[get_current_user] = override_get_current_user

        headers = {"Authorization": "Bearer mock_token"}
        res = self.client.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["email"], "test_user@example.com")
        self.assertEqual(data["display_name"], "Test User")
        self.assertEqual(data["daily_quota_left"], 10)
        self.assertEqual(data["account_tier"], "FREE")


if __name__ == "__main__":
    unittest.main()
