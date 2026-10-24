from __future__ import annotations

import unittest
import uuid
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from botocore.stub import Stubber
from pydantic import ValidationError

from app.config import Settings
from app.models.flashcard import UserFlashcard
from app.services import card_image_service as images


class B2StorageTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.multiple(
            images.settings,
            S3_IMAGE_BUCKET="fixture-private",
            S3_IMAGE_ENDPOINT_URL="https://s3.us-east-005.backblazeb2.com",
            S3_IMAGE_ACCESS_KEY_ID="fixture-b2-id",
            S3_IMAGE_SECRET_ACCESS_KEY="fixture-b2-secret",
            S3_IMAGE_REGION="ap-southeast-1",
            AWS_ACCESS_KEY_ID="fixture-aws-id",
            AWS_SECRET_ACCESS_KEY="fixture-aws-secret",
        )
        self.config.start()
        self.addCleanup(self.config.stop)
        images.close_storage_client()
        self.addCleanup(images.close_storage_client)
        self.card = UserFlashcard.model_construct(user_id=uuid.uuid4())
        self.card.image_key = (
            f"user-images/{self.card.user_id.hex}/{self.card.id.hex}/{uuid.uuid4().hex}.jpg"
        )

    def test_b2_signed_url_uses_dedicated_credentials_endpoint_and_region(self):
        url = urlsplit(images._signed_url(self.card))
        query = parse_qs(url.query)
        self.assertEqual(url.netloc, "s3.us-east-005.backblazeb2.com")
        self.assertEqual(url.path, f"/fixture-private/{self.card.image_key}")
        self.assertIn("fixture-b2-id/", query["X-Amz-Credential"][0])
        self.assertIn("/us-east-005/s3/aws4_request", query["X-Amz-Credential"][0])
        self.assertNotIn("fixture-aws-id", url.query)
        client = images._s3_client()
        self.assertEqual(client.meta.config.request_checksum_calculation, "when_required")
        self.assertEqual(client.meta.config.response_checksum_validation, "when_required")

    def test_missing_b2_key_never_falls_back_to_aws_credentials_or_metadata(self):
        for field in ("S3_IMAGE_ACCESS_KEY_ID", "S3_IMAGE_SECRET_ACCESS_KEY"):
            with (
                self.subTest(field=field),
                patch.object(images.settings, field, ""),
                patch.object(images.boto3, "client") as create,
                self.assertRaisesRegex(images.CardImageError, "IMAGE_STORAGE_NOT_CONFIGURED"),
            ):
                images._s3_client()
            create.assert_not_called()

    def test_empty_endpoint_preserves_aws_s3_configuration(self):
        with (
            patch.object(images.settings, "S3_IMAGE_ENDPOINT_URL", ""),
            patch.object(images.boto3, "client", return_value=MagicMock()) as create,
        ):
            images._s3_client()
        data = create.call_args.kwargs
        self.assertIsNone(data["endpoint_url"])
        self.assertEqual(data["aws_access_key_id"], "fixture-aws-id")
        self.assertEqual(data["aws_secret_access_key"], "fixture-aws-secret")
        self.assertEqual(data["region_name"], "ap-southeast-1")
        self.assertEqual(data["config"].s3["addressing_style"], "virtual")

    def test_real_sdk_upload_and_version_deletion_do_not_touch_similar_keys(self):
        client = images._s3_client()
        key = self.card.image_key
        with Stubber(client) as stub:
            stub.add_response(
                "put_object",
                {"VersionId": "v1"},
                {
                    "Bucket": "fixture-private",
                    "Key": key,
                    "Body": b"fixture",
                    "ContentType": "image/jpeg",
                    "CacheControl": "private, max-age=300",
                    "ServerSideEncryption": "AES256",
                },
            )
            images._put_object(key, b"fixture")
            stub.add_response(
                "list_object_versions",
                {
                    "IsTruncated": True,
                    "NextKeyMarker": key,
                    "NextVersionIdMarker": "marker",
                    "Versions": [
                        {"Key": key, "VersionId": "v1"},
                        {"Key": key + ".other", "VersionId": "foreign"},
                    ],
                    "DeleteMarkers": [{"Key": key, "VersionId": "hidden"}],
                },
                {"Bucket": "fixture-private", "Prefix": key},
            )
            stub.add_response(
                "list_object_versions",
                {
                    "IsTruncated": False,
                    "Versions": [{"Key": key, "VersionId": "v2"}],
                },
                {
                    "Bucket": "fixture-private",
                    "Prefix": key,
                    "KeyMarker": key,
                    "VersionIdMarker": "marker",
                },
            )
            for version in ("v1", "hidden", "v2"):
                stub.add_response(
                    "delete_object",
                    {},
                    {
                        "Bucket": "fixture-private",
                        "Key": key,
                        "VersionId": version,
                    },
                )
            images._delete_object(self.card)
            stub.assert_no_pending_responses()

    def test_listing_failure_is_reported_without_deleting_or_hiding_anything(self):
        client = images._s3_client()
        with Stubber(client) as stub:
            stub.add_client_error(
                "list_object_versions", service_error_code="AccessDenied", http_status_code=403
            )
            with self.assertRaisesRegex(images.CardImageError, "IMAGE_STORAGE_UNAVAILABLE"):
                images._delete_object(self.card)
            stub.assert_no_pending_responses()

    def test_foreign_object_key_is_not_listed_or_deleted(self):
        self.card.image_key = f"user-images/{uuid.uuid4().hex}/foreign.jpg"
        with patch.object(images, "_s3_client") as client:
            images._delete_object(self.card)
        client.assert_not_called()

    def test_only_https_backblaze_endpoints_are_allowed_and_keys_are_redacted(self):
        for endpoint in (
            "http://s3.us-east-005.backblazeb2.com",
            "https://localhost",
            "https://s3.us-east-005.backblazeb2.com.evil.test",
            "https://user:pass@s3.us-east-005.backblazeb2.com",
            "https://s3.us-east-005.backblazeb2.com/path",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValidationError):
                Settings(_env_file=None, SECRET_KEY="fixture", S3_IMAGE_ENDPOINT_URL=endpoint)
        config = Settings(
            _env_file=None,
            SECRET_KEY="fixture",
            S3_IMAGE_ACCESS_KEY_ID="private-id-value",
            S3_IMAGE_SECRET_ACCESS_KEY="private-secret-value",
        )
        self.assertNotIn("private-id-value", repr(config))
        self.assertNotIn("private-secret-value", repr(config))
