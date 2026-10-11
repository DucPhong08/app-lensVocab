"""Opt-in integration tests. Use only a disposable local Mongo replica set.

LENSVOCAB_TEST_MONGO_URL=mongodb://127.0.0.1:27028/?directConnection=true python -m unittest tests.test_mongo_integration -v
"""

import asyncio
import os
import unittest
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from app.models.flashcard import FlashcardStatus, GlobalFlashcard, UserFlashcard
from app.models.review import ReviewLog
from app.models.setting import SystemSetting
from app.models.user import User
from app.schemas.flashcard import ConfirmFlashcardRequest
from app.services.cache_service import FlashcardPayload
from app.services.flashcard_service import DraftExpiredError, confirm_card
from app.services.quota_service import QuotaExceededError, check_and_consume_quota
from app.services.review_service import ReviewConflictError, fetch_today_queue, submit_review


@unittest.skipUnless(
    os.getenv("LENSVOCAB_TEST_MONGO_URL"), "Disposable Mongo replica set not configured"
)
class MongoIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = os.environ["LENSVOCAB_TEST_MONGO_URL"]
        if urlsplit(url).hostname not in {"localhost", "127.0.0.1"}:
            raise RuntimeError("Integration tests require an explicitly local disposable MongoDB")
        self.client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
        # A new namespace per test; no access to the application database or .env.
        self.database = "lensvocab_test_" + uuid.uuid4().hex
        await init_beanie(
            database=self.client[self.database],
            document_models=[User, UserFlashcard, GlobalFlashcard, ReviewLog, SystemSetting],
        )
        self.user = await User(email="test@example.com", hashed_password="offline").insert()
        self.global_card = await GlobalFlashcard(
            keyword="chair", meaning_vi="ghế", example_1="A chair.", example_2="My chair."
        ).insert()
        self.card = await UserFlashcard(
            user_id=self.user.id,
            global_flashcard_id=self.global_card.id,
            status=FlashcardStatus.CONFIRMED,
        ).insert()
        self.redis = AsyncMock()
        self.settings = SystemSetting.model_construct(id="global_config")

    async def asyncTearDown(self):
        # Only remove the exact test-created namespace, never the app database.
        await self.client.drop_database(self.database)
        self.client.close()

    async def test_review_rolls_back_when_log_insert_fails(self):
        with (
            patch("app.services.review_service.get_motor_client", return_value=self.client),
            patch.object(
                ReviewLog, "insert", AsyncMock(side_effect=RuntimeError("simulated write failure"))
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                await submit_review(self.user.id, self.card.id, 4, uuid.uuid4())
        saved = await UserFlashcard.get(self.card.id)
        self.assertEqual(saved.repetitions, 0)
        self.assertEqual(saved.total_reviews, 0)
        self.assertEqual(await ReviewLog.count(), 0)

    async def test_concurrent_replay_changes_sm2_once_and_rejects_conflicting_quality(self):
        review_id = uuid.uuid4()
        with patch("app.services.review_service.get_motor_client", return_value=self.client):
            results = await asyncio.gather(
                *(submit_review(self.user.id, self.card.id, 4, review_id) for _ in range(5))
            )
            self.assertTrue(all(result == results[0] for result in results))
            with self.assertRaises(ReviewConflictError):
                await submit_review(self.user.id, self.card.id, 1, review_id)
        saved = await UserFlashcard.get(self.card.id)
        self.assertEqual(saved.total_reviews, 1)
        self.assertEqual(saved.repetitions, 1)
        self.assertEqual(await ReviewLog.count(), 1)

    async def test_concurrent_first_scans_do_not_reset_quota_twice(self):
        users = [await User.get(self.user.id) for _ in range(15)]
        with patch(
            "app.services.quota_service.fetch_system_settings",
            AsyncMock(return_value=self.settings),
        ):
            results = await asyncio.gather(
                *(check_and_consume_quota(user, self.redis) for user in users),
                return_exceptions=True,
            )
        self.assertEqual(sum(result is None for result in results), 10)
        self.assertEqual(sum(isinstance(result, QuotaExceededError) for result in results), 5)
        user = await User.get(self.user.id)
        self.assertEqual(user.daily_quota_left, 0)

    async def test_existing_day_balance_preserved_and_reset_only_next_day(self):
        from datetime import UTC, datetime

        today = datetime.now(UTC).date()
        await self.user.set({"quota_reset_date": today, "daily_quota_left": 2})
        with patch(
            "app.services.quota_service.fetch_system_settings",
            AsyncMock(return_value=self.settings),
        ):
            await check_and_consume_quota(self.user, self.redis)
            self.assertEqual((await User.get(self.user.id)).daily_quota_left, 1)
            await self.user.set({"quota_reset_date": today - timedelta(days=1)})
            await check_and_consume_quota(self.user, self.redis)
            self.assertEqual((await User.get(self.user.id)).daily_quota_left, 9)

    async def test_confirm_ignores_client_content_and_uses_server_draft(self):
        payload = FlashcardPayload(
            "table", "/table/", "bàn", "A table.", "My table.", ["desk"], None, "bedrock", True
        )
        with patch(
            "app.services.flashcard_service._get_from_redis", AsyncMock(return_value=payload)
        ):
            result = await confirm_card(
                self.user.id,
                ConfirmFlashcardRequest(keyword=" Table ", meaning_vi="forged"),
                self.redis,
            )
        self.assertEqual(result.meaning_vi, "bàn")
        saved = await GlobalFlashcard.get(result.global_flashcard_id)
        self.assertIsNone(saved.embedding)
        again = await confirm_card(
            self.user.id, ConfirmFlashcardRequest(keyword="table"), self.redis
        )
        self.assertEqual(result.user_flashcard_id, again.user_flashcard_id)
        with patch("app.services.flashcard_service._get_from_redis", AsyncMock(return_value=None)):
            with self.assertRaises(DraftExpiredError):
                await confirm_card(
                    self.user.id, ConfirmFlashcardRequest(keyword="missing"), self.redis
                )

    async def test_queue_caps_before_fetch_and_returns_oldest_first(self):
        self.user.preferences.daily_review_goal = 2
        await self.card.set({"next_review_date": date.today() - timedelta(days=1)})
        for i in range(2, 5):
            card = await GlobalFlashcard(
                keyword=f"word{i}", meaning_vi="nghĩa", example_1="One.", example_2="Two."
            ).insert()
            await UserFlashcard(
                user_id=self.user.id,
                global_flashcard_id=card.id,
                status=FlashcardStatus.CONFIRMED,
                next_review_date=date.today() - timedelta(days=i),
            ).insert()
        with patch(
            "app.services.review_service.fetch_system_settings",
            AsyncMock(return_value=self.settings),
        ):
            queue = await fetch_today_queue(self.user)
        self.assertEqual([card.keyword for card in queue], ["word4", "word3"])
