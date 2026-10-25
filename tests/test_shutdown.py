import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from app.main import app, lifespan


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_exception_in_lifespan_still_drains_jobs_before_closing_clients(self):
        events = []

        async def record(name):
            events.append(name)

        with ExitStack() as stack:
            for name in ["get_redis_pool", "get_motor_client"]:
                stack.enter_context(patch("app.main." + name))
            stack.enter_context(patch("app.main.setup_beanie", AsyncMock()))
            stack.enter_context(
                patch(
                    "app.services.card_image_service.finish_image_operations",
                    new=lambda: record("images_finished"),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.cache_service.finish_generation",
                    new=lambda: record("generation_finished"),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.card_image_service.close_storage_client",
                    side_effect=lambda: events.append("storage_closed"),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.ai_service.close_aws_clients",
                    side_effect=lambda: events.append("aws_closed"),
                )
            )
            stack.enter_context(
                patch("app.main.close_redis_pool", new=lambda: record("redis_closed"))
            )
            stack.enter_context(
                patch(
                    "app.main.close_motor_client", side_effect=lambda: events.append("mongo_closed")
                )
            )
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                async with lifespan(app):
                    raise RuntimeError("fixture")
        self.assertEqual(
            events,
            [
                "images_finished",
                "generation_finished",
                "storage_closed",
                "aws_closed",
                "redis_closed",
                "mongo_closed",
            ],
        )
