from __future__ import annotations

import io
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from PIL import Image
from pydantic import ValidationError

from app.dependencies.auth import get_current_user
from app.main import app
from app.models.flashcard import UserFlashcard
from app.models.user import User
from app.schemas.flashcard import CropBox
from app.services import card_image_service as images


def photo(size=(40, 20), orientation=None):
    image = Image.new("RGB", size, "red")
    image.paste("blue", (size[0] // 2, 0, size[0], size[1]))
    output = io.BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[274] = orientation
        exif[270] = "private camera metadata"
    image.save(output, format="JPEG", exif=exif)
    return output.getvalue()


class ImageProcessingTests(unittest.TestCase):
    def test_crops_the_selected_object_instead_of_the_whole_scene(self):
        data = images.prepare_image(photo(), CropBox(left=0.5, top=0, width=0.5, height=1))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (20, 20))
            red, _, blue = image.getpixel((10, 10))
            self.assertGreater(blue, red)

    def test_orientation_is_applied_before_crop_and_metadata_is_removed(self):
        data = images.prepare_image(
            photo(orientation=6), CropBox(left=0, top=0, width=0.5, height=1)
        )
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (10, 40))
            self.assertEqual(dict(image.getexif()), {})
            self.assertNotIn("private camera metadata", str(image.info))
            self.assertEqual(image.format, "JPEG")

    def test_png_crop_uses_raw_rekognition_coordinates_before_exif_rotation(self):
        output = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (40, 20), "blue").save(output, "PNG", exif=exif)
        data = images.prepare_image(output.getvalue(), CropBox(left=0, top=0, width=0.25, height=1))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (20, 10))
            self.assertEqual(dict(image.getexif()), {})

    def test_large_photos_are_resized_without_changing_aspect_ratio(self):
        with Image.open(io.BytesIO(images.prepare_image(photo((3200, 1600))))) as image:
            self.assertEqual(image.size, (1600, 800))

    def test_transparent_png_is_flattened_on_white(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (10, 10), (255, 0, 0, 0)).save(buffer, "PNG")
        with Image.open(io.BytesIO(images.prepare_image(buffer.getvalue()))) as image:
            self.assertEqual(image.mode, "RGB")
            self.assertGreater(min(image.getpixel((5, 5))), 245)

    def test_non_images_and_empty_uploads_are_rejected(self):
        for data, code in (
            (b"", "FILE_EMPTY"),
            (b"<svg>private</svg>", "INVALID_IMAGE_DATA"),
            (b"not-jpeg", "INVALID_IMAGE_DATA"),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(images.CardImageError, code):
                images.prepare_image(data)

    def test_compressed_byte_limit_and_decoded_pixel_limit_are_independent(self):
        with patch.object(images, "MAX_IMAGE_BYTES", 10):
            with self.assertRaisesRegex(images.CardImageError, "IMAGE_TOO_LARGE"):
                images.prepare_image(b"x" * 11)
        with patch.object(images, "MAX_IMAGE_PIXELS", 100):
            with self.assertRaisesRegex(images.CardImageError, "IMAGE_PIXEL_LIMIT_EXCEEDED"):
                images.prepare_image(photo())

    def test_crop_cannot_escape_the_image_or_contain_nan(self):
        for values in ({"left": -0.1}, {"left": 0.9}, {"width": 0}, {"height": float("nan")}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                CropBox.model_validate(
                    {**{"left": 0, "top": 0, "width": 0.5, "height": 1}, **values}
                )


class ImageStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.card = UserFlashcard.model_construct(user_id=uuid.uuid4())
        self.client = MagicMock()
        self.client.generate_presigned_url.return_value = "https://private.example/signed-image"
        self.query = MagicMock()
        self.query.update = AsyncMock(return_value=SimpleNamespace(matched_count=1))
        self.patchers = [
            patch.object(images.settings, "S3_IMAGE_BUCKET", "fixture-private-bucket"),
            patch.object(images, "_s3_client", return_value=self.client),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def upload(self):
        return await images.set_card_image(self.card.user_id, self.card.id, photo())

    async def test_foreign_card_is_rejected_before_decoding_or_calling_storage(self):
        with (
            patch.object(UserFlashcard, "find_one", AsyncMock(return_value=None)),
            patch.object(images, "prepare_image") as prepare,
        ):
            for action in (
                self.upload(),
                images.get_card_image(self.card.user_id, self.card.id),
                images.delete_card_image(self.card.user_id, self.card.id),
            ):
                with self.assertRaisesRegex(images.CardImageError, "FLASHCARD_NOT_FOUND"):
                    await action
        prepare.assert_not_called()
        self.client.assert_not_called()
        self.client.put_object.assert_not_called()

    async def test_upload_is_private_and_object_key_belongs_to_user_and_card(self):
        with patch.object(
            UserFlashcard, "find_one", side_effect=[AsyncMock(return_value=self.card)(), self.query]
        ) as find:
            result = await self.upload()
        self.assertTrue(result.has_image)
        self.assertEqual(result.image_url, "https://private.example/signed-image")
        data = self.client.put_object.call_args.kwargs
        self.assertTrue(
            data["Key"].startswith(f"user-images/{self.card.user_id.hex}/{self.card.id.hex}/")
        )
        self.assertNotIn("ACL", data)
        self.assertEqual(data["ContentType"], "image/jpeg")
        self.assertEqual(data["ServerSideEncryption"], "AES256")
        self.assertEqual(find.call_args_list[1].args[0]["image_key"], None)

    async def test_missing_bucket_disables_upload_without_touching_existing_card(self):
        with (
            patch.object(UserFlashcard, "find_one", AsyncMock(return_value=self.card)),
            patch.object(images.settings, "S3_IMAGE_BUCKET", ""),
        ):
            with self.assertRaisesRegex(images.CardImageError, "IMAGE_STORAGE_NOT_CONFIGURED"):
                await self.upload()
        self.client.put_object.assert_not_called()

    async def test_update_race_removes_only_the_new_unreferenced_object(self):
        self.query.update.return_value = SimpleNamespace(matched_count=0)
        with patch.object(
            UserFlashcard, "find_one", side_effect=[AsyncMock(return_value=self.card)(), self.query]
        ):
            with self.assertRaisesRegex(images.CardImageError, "IMAGE_UPDATE_CONFLICT"):
                await self.upload()
        self.assertEqual(
            self.client.delete_object.call_args.kwargs["Key"],
            self.client.put_object.call_args.kwargs["Key"],
        )

    async def test_database_failure_cleans_up_new_object_and_propagates_the_failure(self):
        self.query.update.side_effect = RuntimeError("fixture-db-failure")
        with patch.object(
            UserFlashcard, "find_one", side_effect=[AsyncMock(return_value=self.card)(), self.query]
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture-db-failure"):
                await self.upload()
        self.client.delete_object.assert_called_once()

    async def test_replace_removes_only_the_previous_owned_object(self):
        old = f"user-images/{self.card.user_id.hex}/{self.card.id.hex}/{uuid.uuid4().hex}.jpg"
        self.card.image_key = old
        with patch.object(
            UserFlashcard, "find_one", side_effect=[AsyncMock(return_value=self.card)(), self.query]
        ):
            await self.upload()
        self.client.delete_object.assert_called_once_with(Bucket="fixture-private-bucket", Key=old)

    async def test_signing_never_grants_access_to_another_users_stored_key(self):
        self.card.image_key = (
            f"user-images/{uuid.uuid4().hex}/{self.card.id.hex}/{uuid.uuid4().hex}.jpg"
        )
        urls = await images.card_image_urls([self.card])
        self.assertEqual(urls, {})
        self.client.generate_presigned_url.assert_not_called()

    async def test_delete_without_a_photo_is_idempotent(self):
        with patch.object(UserFlashcard, "find_one", AsyncMock(return_value=self.card)):
            result = await images.delete_card_image(self.card.user_id, self.card.id)
        self.assertFalse(result.has_image)
        self.client.delete_object.assert_not_called()


class ImageRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_routes_require_authentication(self):
        path = f"/flashcards/{uuid.uuid4()}/image"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            self.assertEqual((await client.get(path)).status_code, 401)
            self.assertEqual((await client.delete(path)).status_code, 401)
            self.assertEqual(
                (
                    await client.put(path, files={"file": ("a.jpg", photo(), "image/jpeg")})
                ).status_code,
                401,
            )

    async def test_invalid_crop_and_mime_do_not_reach_storage(self):
        user = User.model_construct()
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            with patch("app.routers.flashcards.set_card_image", AsyncMock()) as store:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    path = f"/flashcards/{uuid.uuid4()}/image"
                    result = await client.put(
                        path,
                        files={"file": ("a.jpg", photo(), "image/jpeg")},
                        data={"crop": '{"left": -1}'},
                    )
                    self.assertEqual(result.status_code, 400)
                    result = await client.put(
                        path, files={"file": ("a.svg", b"<svg/>", "image/svg+xml")}
                    )
                    self.assertEqual(result.status_code, 400)
            store.assert_not_awaited()
        finally:
            app.dependency_overrides.clear()


class S3SDKContractTests(unittest.TestCase):
    def test_real_sdk_accepts_private_upload_parameters_and_signs_scoped_get_urls(self):
        import boto3
        from botocore.config import Config
        from botocore.stub import Stubber

        client = boto3.client(
            "s3",
            region_name="ap-southeast-1",
            aws_access_key_id="fixture-access",
            aws_secret_access_key="fixture-secret",
            config=Config(signature_version="s3v4"),
        )
        content = b"fixture-jpeg"
        card = UserFlashcard.model_construct(user_id=uuid.uuid4())
        card.image_key = f"user-images/{card.user_id.hex}/{card.id.hex}/{uuid.uuid4().hex}.jpg"
        try:
            with (
                Stubber(client) as stub,
                patch.object(images, "_s3_client", return_value=client),
                patch.object(images.settings, "S3_IMAGE_BUCKET", "fixture-private"),
            ):
                stub.add_response(
                    "put_object",
                    {},
                    {
                        "Bucket": "fixture-private",
                        "Key": card.image_key,
                        "Body": content,
                        "ContentType": "image/jpeg",
                        "CacheControl": "private, max-age=300",
                        "ServerSideEncryption": "AES256",
                    },
                )
                images._put_object(card.image_key, content)
                url = images._signed_url(card)
                self.assertTrue(url.startswith("https://"))
                self.assertIn("X-Amz-Signature=", url)
                self.assertIn(card.id.hex, url)
                stub.assert_no_pending_responses()
        finally:
            client.close()
