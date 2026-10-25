import asyncio
import threading
import unittest
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymongo.errors import AutoReconnect

from app.models.flashcard import UserFlashcard
from app.services import card_image_service as images


class ImageLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.card = UserFlashcard.model_construct(user_id=uuid.uuid4())
        self.old = f"user-images/{self.card.user_id.hex}/{self.card.id.hex}/{uuid.uuid4().hex}.jpg"
        self.key = self.old
        self.objects = {self.old}
        self.events = []
        self.failure_before = False
        self.failure_after = False
        self.fail_readback = False
        self.reads = 0
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

        async def owned(*args):
            self.reads += 1
            if self.fail_readback and self.reads > 1:
                raise AutoReconnect("offline readback")
            return self.card.model_copy(update={"image_key": self.key})

        async def update(value):
            if self.failure_before:
                raise AutoReconnect("offline before commit")
            self.key = value["$set"]["image_key"]
            self.events.append("db_updated")
            if self.failure_after:
                raise AutoReconnect("acknowledgement lost")
            return SimpleNamespace(matched_count=1)

        def put(key, data):
            self.objects.add(key)
            self.events.append("object_written")

        def delete(card):
            self.objects.discard(card.image_key)
            self.events.append("object_deleted")

        self.stack.enter_context(
            patch.object(images.settings, "S3_IMAGE_BUCKET", "offline-fixture")
        )
        self.stack.enter_context(patch.object(images, "_owned_card", side_effect=owned))
        self.stack.enter_context(patch.object(images, "prepare_image", return_value=b"fixture"))
        self.put = self.stack.enter_context(patch.object(images, "_put_object", side_effect=put))
        self.delete = self.stack.enter_context(
            patch.object(images, "_delete_object", side_effect=delete)
        )
        self.stack.enter_context(
            patch.object(
                images, "card_image_urls", AsyncMock(return_value={self.card.id: "private-url"})
            )
        )
        self.query = MagicMock()
        self.query.update = AsyncMock(side_effect=update)
        self.stack.enter_context(patch.object(UserFlashcard, "find_one", return_value=self.query))
        if hasattr(images, "CLEANUP_RETRY_DELAY"):
            self.stack.enter_context(patch.object(images, "CLEANUP_RETRY_DELAY", 0.001))

    async def asyncTearDown(self):
        if hasattr(images, "finish_image_operations"):
            await images.finish_image_operations()

    async def upload(self):
        return await images.set_card_image(
            self.card.user_id, self.card.id, b"fixture", style="sticker"
        )

    async def test_cancelled_upload_finishes_commit_before_cleaning_old_image(self):
        started = threading.Event()
        release = threading.Event()
        done = threading.Event()

        def put(key, data):
            started.set()
            release.wait(2)
            self.objects.add(key)
            done.set()

        self.put.side_effect = put
        request = asyncio.create_task(self.upload())
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request
        release.set()
        self.assertTrue(await asyncio.to_thread(done.wait, 1))
        if hasattr(images, "finish_image_operations"):
            await images.finish_image_operations()
        self.assertNotEqual(self.key, self.old)
        self.assertIn(self.key, self.objects)
        self.assertNotIn(self.old, self.objects)

    async def test_delete_db_failure_preserves_referenced_object(self):
        self.failure_before = True
        with self.assertRaises(AutoReconnect):
            await images.delete_card_image(self.card.user_id, self.card.id)
        self.assertEqual(self.key, self.old)
        self.assertIn(self.old, self.objects)
        self.delete.assert_not_called()

    async def test_delete_detaches_db_before_storage_and_is_idempotent(self):
        result = await images.delete_card_image(self.card.user_id, self.card.id)
        self.assertFalse(result.has_image)
        self.assertEqual(self.events, ["db_updated", "object_deleted"])
        self.assertIsNone(self.key)
        await images.delete_card_image(self.card.user_id, self.card.id)
        self.delete.assert_called_once()

    async def test_lost_update_ack_does_not_delete_new_live_image(self):
        self.failure_after = True
        result = await self.upload()
        self.assertTrue(result.has_image)
        self.assertIn(self.key, self.objects)
        self.assertNotIn(self.old, self.objects)

    async def test_failed_update_removes_new_object_only_after_readback(self):
        self.failure_before = True
        with self.assertRaises(AutoReconnect):
            await self.upload()
        self.assertEqual(self.objects, {self.old})
        self.assertEqual(self.key, self.old)
        self.assertGreaterEqual(self.reads, 2)

    async def test_unknown_write_outcome_keeps_potentially_live_image(self):
        self.failure_after = True
        self.fail_readback = True
        with self.assertRaisesRegex(images.CardImageError, "IMAGE_UPDATE_UNCERTAIN"):
            await self.upload()
        self.assertIn(self.key, self.objects)
        self.assertIn(self.old, self.objects)
        self.delete.assert_not_called()

    async def test_cleanup_retries_transient_storage_failure(self):
        self.delete.side_effect = [images.CardImageError("IMAGE_STORAGE_UNAVAILABLE", 503), None]
        await images.delete_card_image(self.card.user_id, self.card.id)
        self.assertEqual(self.delete.call_count, 2)
        self.assertIsNone(self.key)

    async def test_permanent_cleanup_failure_does_not_break_saved_pointer(self):
        self.delete.side_effect = images.CardImageError("IMAGE_STORAGE_UNAVAILABLE", 503)
        result = await self.upload()
        self.assertTrue(result.has_image)
        self.assertIn(self.key, self.objects)
        self.assertEqual(self.delete.call_count, 3)

    async def test_cancelled_delete_finishes_after_database_update(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        update = self.query.update.side_effect

        async def paused(value):
            entered.set()
            await release.wait()
            return await update(value)

        self.query.update.side_effect = paused
        request = asyncio.create_task(images.delete_card_image(self.card.user_id, self.card.id))
        await asyncio.wait_for(entered.wait(), 1)
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request
        release.set()
        await images.finish_image_operations()
        self.assertIsNone(self.key)
        self.assertNotIn(self.old, self.objects)

    async def test_lost_delete_ack_is_reconciled_before_cleaning(self):
        self.failure_after = True
        result = await images.delete_card_image(self.card.user_id, self.card.id)
        self.assertFalse(result.has_image)
        self.assertIsNone(self.key)
        self.assertNotIn(self.old, self.objects)

    async def test_concurrent_update_conflict_never_deletes_live_old_image(self):
        self.query.update.side_effect = None
        self.query.update.return_value = SimpleNamespace(matched_count=0)
        with self.assertRaisesRegex(images.CardImageError, "IMAGE_UPDATE_CONFLICT"):
            await images.delete_card_image(self.card.user_id, self.card.id)
        self.assertEqual(self.objects, {self.old})
        self.delete.assert_not_called()
