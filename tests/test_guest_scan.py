import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import Request
from starlette.testclient import TestClient

from app.main import app
from app.schemas.vision import ScanResponse
from app.services.guest_quota_service import GuestScanLimitError, client_ip, consume_guest_quota


class TestGuestScan(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.image = {"file": ("photo.jpg", io.BytesIO(b"valid image bytes"), "image/jpeg")}

    @patch("app.routers.vision.execute_vision_scan", new_callable=AsyncMock)
    @patch("app.routers.vision.consume_guest_quota", new_callable=AsyncMock)
    def test_guest_scans_without_jwt_and_does_not_create_user(self, quota, scan):
        scan.return_value = ScanResponse(status="OK", keyword="chair", example_1="A chair.")
        response = self.client.post("/vision/scan/guest", files=self.image)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["keyword"], "chair")
        self.assertIsNone(scan.await_args.args[1])
        quota.assert_awaited_once()
        self.assertEqual(self.client.post("/vision/scan").status_code, 401)
        self.assertEqual(self.client.get("/flashcards").status_code, 401)
        self.assertEqual(self.client.post("/flashcards/confirm", json={}).status_code, 401)
        self.assertIn("security", app.openapi()["paths"]["/vision/scan"]["post"])
        self.assertNotIn("security", app.openapi()["paths"]["/vision/scan/guest"]["post"])

    @patch("app.routers.vision.execute_vision_scan", new_callable=AsyncMock)
    @patch("app.routers.vision.consume_guest_quota", new_callable=AsyncMock)
    def test_quota_denial_does_not_run_aws(self, quota, scan):
        quota.side_effect = GuestScanLimitError("GUEST_QUOTA_EXCEEDED")
        response = self.client.post("/vision/scan/guest", files=self.image)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["detail"], "GUEST_QUOTA_EXCEEDED")
        scan.assert_not_awaited()

    @patch("app.routers.vision.consume_guest_quota", new_callable=AsyncMock)
    def test_rejects_invalid_or_oversized_images_before_quota(self, quota):
        wrong_type = {"file": ("file.txt", io.BytesIO(b"text"), "text/plain")}
        self.assertEqual(self.client.post("/vision/scan/guest", files=wrong_type).status_code, 400)
        too_large = {"file": ("photo.jpg", io.BytesIO(b"0" * (5 * 1024 * 1024 + 1)), "image/jpeg")}
        self.assertEqual(self.client.post("/vision/scan/guest", files=too_large).status_code, 413)
        quota.assert_not_awaited()

    @patch("app.routers.vision.execute_vision_scan", new_callable=AsyncMock)
    @patch("app.routers.vision.consume_guest_quota", new_callable=AsyncMock)
    def test_aws_failure_is_sanitized(self, quota, scan):
        from botocore.exceptions import ClientError
        scan.side_effect = ClientError({"Error": {"Code": "ThrottlingException", "Message": "internal AWS detail"}}, "DetectLabels")
        response = self.client.post("/vision/scan/guest", files=self.image)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("internal AWS detail", response.text)

    def test_client_ip_does_not_trust_unverified_forwarded_header(self):
        request = Request({"type": "http", "method": "POST", "path": "/", "client": ("192.0.2.3", 80), "headers": [(b"x-forwarded-for", b"198.51.100.2, 203.0.113.4")]})
        with patch.dict("os.environ", {"RENDER": "false"}):
            self.assertEqual(client_ip(request), "192.0.2.3")
        with patch.dict("os.environ", {"RENDER": "true"}):
            self.assertEqual(client_ip(request), "203.0.113.4")

    @patch("app.services.guest_quota_service.fetch_system_settings", new_callable=AsyncMock)
    def test_redis_budget_denies_after_limit_and_respects_maintenance(self, get_settings):
        request = Request({"type": "http", "method": "POST", "path": "/", "client": ("192.0.2.3", 80), "headers": []})
        get_settings.return_value = SimpleNamespace(maintenance_mode=False)
        redis = SimpleNamespace(eval=AsyncMock(side_effect=[0, 1]))
        asyncio.run(consume_guest_quota(request, redis))
        self.assertEqual(redis.eval.await_args.args[1], 3)
        with self.assertRaisesRegex(GuestScanLimitError, "GUEST_QUOTA_EXCEEDED"):
            asyncio.run(consume_guest_quota(request, redis))
        get_settings.return_value = SimpleNamespace(maintenance_mode=True)
        with self.assertRaisesRegex(GuestScanLimitError, "MAINTENANCE_MODE"):
            asyncio.run(consume_guest_quota(request, redis))
        self.assertEqual(redis.eval.await_count, 2)


if __name__ == "__main__":
    unittest.main()
