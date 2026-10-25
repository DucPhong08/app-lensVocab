import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import cache_service as service


class GenerationCoalescingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.redis = object()

        async def generate(keyword):
            self.entered.set()
            await self.release.wait()
            return SimpleNamespace(
                pronunciation=None,
                meaning_vi=keyword,
                example_1="A.",
                example_2="B.",
                related_words=["test"],
            )

        self.generate = self.stack.enter_context(
            patch.object(service, "generate_flashcard_content", side_effect=generate)
        )
        self.audio = self.stack.enter_context(
            patch.object(service, "synthesize_speech", AsyncMock(return_value="audio"))
        )
        self.stack.enter_context(
            patch.object(service, "_get_from_redis", AsyncMock(return_value=None))
        )
        self.stack.enter_context(
            patch.object(service, "_find_exact_mongodb", AsyncMock(return_value=None))
        )
        self.save = self.stack.enter_context(patch.object(service, "_save_to_redis", AsyncMock()))

    async def asyncTearDown(self):
        self.release.set()
        tasks = list(service._inflight.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertFalse(service._inflight)

    async def test_twenty_simultaneous_normalized_words_generate_once(self):
        calls = [
            asyncio.create_task(service.get_flashcard(word, self.redis))
            for word in [" CUP ", "cup"] * 10
        ]
        await self.entered.wait()
        self.release.set()
        results = await asyncio.gather(*calls)
        self.generate.assert_awaited_once_with("cup")
        self.audio.assert_awaited_once_with("cup")
        self.save.assert_awaited_once()
        self.assertTrue(all(item.keyword == "cup" for item in results))
        results[0].related_words.append("changed")
        self.assertEqual(results[1].related_words, ["test"])

    async def test_stream_and_http_share_a_generation(self):
        async def collect():
            return [item async for item in service.stream_flashcard("cup", self.redis)]

        http = asyncio.create_task(service.get_flashcard("cup", self.redis))
        stream = asyncio.create_task(collect())
        await self.entered.wait()
        self.release.set()
        result, events = await asyncio.gather(http, stream)
        self.generate.assert_awaited_once()
        self.assertEqual(events[-1]["data"]["keyword"], result.keyword)
        self.assertEqual(
            [item["event"] for item in events],
            ["status", "status", "vocab_content", "audio_ready", "done"],
        )

    async def test_cancelling_a_waiter_does_not_cancel_the_other(self):
        first = asyncio.create_task(service.get_flashcard("cup", self.redis))
        second = asyncio.create_task(service.get_flashcard("cup", self.redis))
        await self.entered.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.release.set()
        self.assertEqual((await second).keyword, "cup")
        self.generate.assert_awaited_once()

    async def test_all_waiters_cancelled_still_finish_cache_and_cleanup(self):
        caller = asyncio.create_task(service.get_flashcard("cup", self.redis))
        await self.entered.wait()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        jobs = list(service._inflight.values())
        self.release.set()
        await asyncio.gather(*jobs)
        await asyncio.sleep(0)
        self.save.assert_awaited_once()
        self.assertFalse(service._inflight)

    async def test_failure_is_shared_then_a_retry_can_start_again(self):
        async def fail(keyword):
            self.entered.set()
            await self.release.wait()
            raise RuntimeError("AI unavailable")

        self.generate.side_effect = fail
        calls = [asyncio.create_task(service.get_flashcard("cup", self.redis)) for _ in range(3)]
        await self.entered.wait()
        self.release.set()
        results = await asyncio.gather(*calls, return_exceptions=True)
        self.assertTrue(all(isinstance(item, RuntimeError) for item in results))
        self.generate.assert_awaited_once()
        self.generate.side_effect = None
        self.generate.return_value = SimpleNamespace(
            pronunciation=None, meaning_vi="cup", example_1="A", example_2="B", related_words=[]
        )
        self.assertEqual((await service.get_flashcard("cup", self.redis)).keyword, "cup")
        self.assertEqual(self.generate.await_count, 2)

    async def test_distinct_words_are_not_serialized(self):
        calls = [
            asyncio.create_task(service.get_flashcard(word, self.redis)) for word in ["cup", "book"]
        ]
        await self.entered.wait()
        await asyncio.sleep(0)
        self.assertEqual(self.generate.await_count, 2)
        self.release.set()
        self.assertEqual({item.keyword for item in await asyncio.gather(*calls)}, {"cup", "book"})

    async def test_separate_redis_clients_never_share_a_job(self):
        calls = [
            asyncio.create_task(service.get_flashcard("cup", redis))
            for redis in [self.redis, object()]
        ]
        await self.entered.wait()
        await asyncio.sleep(0)
        self.release.set()
        await asyncio.gather(*calls)
        self.assertEqual(self.generate.await_count, 2)

    async def test_cache_filled_before_generation_is_reused(self):
        cached = service.FlashcardPayload("cup", None, "cup", "A", "B", [], None, "redis", True)
        with patch.object(service, "_get_from_redis", AsyncMock(side_effect=[None, cached])):
            self.assertEqual((await service.get_flashcard("cup", self.redis)).source, "redis")
        self.generate.assert_not_awaited()

    async def test_request_clients_using_same_pool_share_a_job(self):
        pool = object()
        clients = [SimpleNamespace(connection_pool=pool), SimpleNamespace(connection_pool=pool)]
        calls = [asyncio.create_task(service.get_flashcard("cup", client)) for client in clients]
        await self.entered.wait()
        await asyncio.sleep(0)
        self.release.set()
        await asyncio.gather(*calls)
        self.generate.assert_awaited_once()
