from __future__ import annotations

import unittest
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymongo import ReturnDocument
from redis.exceptions import RedisError

from app.models.user import AccountTier, User
from app.services.quota_service import (
    MaintenanceModeError,
    QuotaExceededError,
    check_and_consume_quota,
)


class TestQuotaService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 11, 8, 30, tzinfo=UTC)
        self.settings = SimpleNamespace(
            maintenance_mode=False,
            free_daily_quota=2,
            premium_daily_quota=20,
        )
        self.redis = SimpleNamespace(ping=AsyncMock(return_value=True))
        self.collection = MagicMock()
        self.collection.find_one_and_update = AsyncMock()

    def make_user(
        self,
        *,
        remaining: int = 2,
        reset_date: date | None = date(2026, 10, 11),
    ) -> User:
        return User.model_construct(
            id=uuid.uuid4(),
            account_tier=AccountTier.FREE,
            daily_quota_left=remaining,
            quota_reset_date=reset_date,
            is_active=True,
        )

    def service_patches(self):
        return (
            patch(
                "app.services.quota_service.fetch_system_settings",
                new_callable=AsyncMock,
                return_value=self.settings,
            ),
            patch("app.services.quota_service.datetime"),
            patch.object(User, "get_motor_collection", return_value=self.collection),
        )

    async def consume(self, user: User) -> None:
        settings_patch, datetime_patch, collection_patch = self.service_patches()
        with settings_patch, datetime_patch as clock, collection_patch:
            clock.now.return_value = self.now
            await check_and_consume_quota(user, self.redis)

    async def test_atomic_pipeline_uses_durable_counter_instead_of_user_snapshot(self):
        user = self.make_user(remaining=99)
        self.collection.find_one_and_update.return_value = {"daily_quota_left": 1}

        await self.consume(user)

        query, update = self.collection.find_one_and_update.await_args.args
        same_day, durable_remaining, reset_quota = query["$expr"]["$gt"][0]["$cond"]
        self.assertEqual(same_day["$eq"][0], "$quota_reset_date")
        self.assertEqual(durable_remaining, "$daily_quota_left")
        self.assertEqual(reset_quota, 2)
        self.assertEqual(
            update[0]["$set"]["daily_quota_left"]["$subtract"][0], query["$expr"]["$gt"][0]
        )
        self.assertEqual(update[0]["$set"]["daily_quota_left"]["$subtract"][1], 1)
        self.assertEqual(query["$expr"]["$gt"][1], 0)
        self.assertEqual(
            self.collection.find_one_and_update.await_args.kwargs["return_document"],
            ReturnDocument.AFTER,
        )
        self.assertEqual(user.daily_quota_left, 1)
        self.redis.ping.assert_awaited_once_with()

    async def test_two_stale_snapshots_receive_distinct_atomic_results(self):
        user_id = uuid.uuid4()
        first = self.make_user(remaining=2)
        second = self.make_user(remaining=2)
        first.id = user_id
        second.id = user_id
        self.collection.find_one_and_update.side_effect = [
            {"daily_quota_left": 1},
            {"daily_quota_left": 0},
        ]

        await self.consume(first)
        await self.consume(second)

        self.assertEqual(first.daily_quota_left, 1)
        self.assertEqual(second.daily_quota_left, 0)
        self.assertEqual(self.collection.find_one_and_update.await_count, 2)

    async def test_new_day_resets_inside_same_atomic_decrement(self):
        user = self.make_user(remaining=0, reset_date=date(2026, 10, 10))
        self.collection.find_one_and_update.return_value = {"daily_quota_left": 1}

        await self.consume(user)

        query, update = self.collection.find_one_and_update.await_args.args
        reset_quota = query["$expr"]["$gt"][0]["$cond"][2]
        self.assertEqual(reset_quota, self.settings.free_daily_quota)
        self.assertEqual(update[0]["$set"]["quota_reset_date"], datetime(2026, 10, 11))
        self.assertEqual(user.quota_reset_date, date(2026, 10, 11))
        self.assertEqual(user.daily_quota_left, 1)
        self.assertFalse(hasattr(self.redis, "delete"))

    async def test_exhausted_quota_does_not_mutate_snapshot(self):
        user = self.make_user(remaining=0)
        self.collection.find_one_and_update.return_value = None

        with self.assertRaisesRegex(QuotaExceededError, "QUOTA_EXCEEDED"):
            await self.consume(user)

        self.assertEqual(user.daily_quota_left, 0)
        self.assertEqual(user.quota_reset_date, date(2026, 10, 11))

    async def test_redis_outage_fails_closed_before_mongo_write(self):
        user = self.make_user()
        self.redis.ping.side_effect = RedisError("redis unavailable")
        settings_patch, datetime_patch, collection_patch = self.service_patches()
        with settings_patch, datetime_patch as clock, collection_patch:
            clock.now.return_value = self.now
            with self.assertRaisesRegex(RedisError, "redis unavailable"):
                await check_and_consume_quota(user, self.redis)

        self.collection.find_one_and_update.assert_not_awaited()

    async def test_maintenance_mode_does_not_touch_redis_or_mongo(self):
        self.settings.maintenance_mode = True
        user = self.make_user()
        settings_patch, datetime_patch, collection_patch = self.service_patches()
        with settings_patch, datetime_patch, collection_patch:
            with self.assertRaisesRegex(MaintenanceModeError, "MAINTENANCE_MODE"):
                await check_and_consume_quota(user, self.redis)

        self.redis.ping.assert_not_awaited()
        self.collection.find_one_and_update.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
