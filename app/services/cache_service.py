from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass, replace
from typing import Any

import redis.asyncio as aioredis
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.models.flashcard import GlobalFlashcard
from app.services.ai_service import (
    generate_flashcard_content,
    synthesize_speech,
)

logger = logging.getLogger(__name__)


async def _safe_synthesize(keyword: str) -> str | None:
    """Wrap synthesize_speech để lỗi Polly không hủy Bedrock khi dùng asyncio.gather."""
    try:
        return await synthesize_speech(keyword)
    except Exception as exc:
        logger.error("polly_tts_failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Data Transfer Object
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class FlashcardPayload:
    keyword: str
    pronunciation: str | None  # /tʃer/
    meaning_vi: str  # cái ghế
    example_1: str  # I sit on a chair.
    example_2: str  # This chair is comfortable.
    related_words: list[str]  # ["seat", "sofa", "stool"]
    audio_base64: str | None  # MP3 audio từ AWS Polly
    source: str  # "redis" | "mongodb" | "bedrock"
    is_draft: bool  # True nếu từ Tầng 3 (chờ user xác nhận)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _payload_from_document(
    doc: GlobalFlashcard, *, source: str, is_draft: bool
) -> FlashcardPayload:
    return FlashcardPayload(
        keyword=doc.keyword,
        pronunciation=doc.pronunciation,
        meaning_vi=doc.meaning_vi,
        example_1=doc.example_1,
        example_2=doc.example_2,
        related_words=doc.related_words,
        audio_base64=doc.audio_base64,
        source=source,
        is_draft=is_draft,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tầng 1 – Redis Exact Match
# ─────────────────────────────────────────────────────────────────────────────


def _redis_key(keyword: str) -> str:
    return f"vocab:{keyword.lower().strip()}"


async def _get_from_redis(redis: aioredis.Redis, keyword: str) -> FlashcardPayload | None:
    raw = await redis.get(_redis_key(keyword))
    if raw is None:
        return None
    data: dict[str, Any] = json.loads(raw)
    data["source"] = "redis"
    return FlashcardPayload(**data)


async def _save_to_redis(redis: aioredis.Redis, payload: FlashcardPayload) -> None:
    # Draft (Tầng 3) và confirmed (Tầng 2) dùng chung TTL này — key giống nhau
    # nên khi user confirm, bản confirmed sẽ ghi đè bản draft tự nhiên.
    await redis.set(
        _redis_key(payload.keyword),
        json.dumps(payload.to_dict()),
        ex=settings.REDIS_CACHE_TTL,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tầng 2 – MongoDB Exact Match
# ─────────────────────────────────────────────────────────────────────────────


async def _find_exact_mongodb(keyword: str) -> GlobalFlashcard | None:
    """Read the exact word when the Redis entry has expired or was evicted."""
    return await GlobalFlashcard.find_one({"keyword": keyword})


# ─────────────────────────────────────────────────────────────────────────────
# Public API – Multi-Tier Cache Resolver (Pure AWS)
# ─────────────────────────────────────────────────────────────────────────────


async def get_flashcard(
    keyword: str,
    redis: aioredis.Redis,
    candidate_keywords: list[str] | None = None,
) -> FlashcardPayload:
    """Resolve the exact keyword; parent labels must never replace the detected object.

    candidate_keywords is retained for existing callers but deliberately ignored.
    """
    normalized = keyword.lower().strip()

    cached = await _lookup(normalized, redis)
    return cached if cached is not None else await _generate_once(normalized, redis)


async def _lookup(normalized: str, redis: aioredis.Redis) -> FlashcardPayload | None:
    # ── Tầng 1: Redis Exact Match (keyword chính) ──────────────────────────────
    cached = await _get_from_redis(redis, normalized)
    if cached is not None:
        logger.info("cache_hit=redis keyword=%s", normalized)
        return cached

    # ── Tầng 2: MongoDB Exact Match (keyword chính) ───────────────────────────
    exact = await _find_exact_mongodb(normalized)
    if exact is not None:
        logger.info("cache_hit=mongodb_exact keyword=%s", normalized)
        payload = _payload_from_document(exact, source="mongodb", is_draft=False)
        await _save_to_redis(redis, payload)
        return payload

    return None


# Only active jobs are retained. Public vocabulary can be shared between users;
# separate event loops/Redis pools never share a result. This is per-process.
_inflight: dict[tuple[asyncio.AbstractEventLoop, int, str], asyncio.Task[FlashcardPayload]] = {}


async def _generate_once(keyword: str, redis: aioredis.Redis) -> FlashcardPayload:
    pool = getattr(redis, "connection_pool", redis)
    key = (asyncio.get_running_loop(), id(pool), keyword)
    task = _inflight.get(key)
    if task is None:
        task = asyncio.create_task(_generate(keyword, redis))
        _inflight[key] = task

        def finished(job: asyncio.Task) -> None:
            if _inflight.get(key) is job:
                del _inflight[key]
            # Observe failures even when every caller disconnects.
            if not job.cancelled():
                job.exception()

        task.add_done_callback(finished)
    # One disconnected/timed-out request must not cancel other callers' work.
    payload = await asyncio.shield(task)
    return replace(payload, related_words=list(payload.related_words))


async def finish_generation() -> None:
    while _inflight:
        await asyncio.gather(*list(_inflight.values()), return_exceptions=True)


async def _generate(keyword: str, redis: aioredis.Redis) -> FlashcardPayload:
    # Close the race with a request that filled the cache after the first lookup.
    cached = await _get_from_redis(redis, keyword)
    if cached is not None:
        return cached
    logger.info("cache_miss=all generating vocabulary and speech")
    generated, audio_b64 = await asyncio.gather(
        generate_flashcard_content(keyword),
        _safe_synthesize(keyword),
    )
    payload = FlashcardPayload(
        keyword=keyword,
        pronunciation=generated.pronunciation,
        meaning_vi=generated.meaning_vi,
        example_1=generated.example_1,
        example_2=generated.example_2,
        related_words=generated.related_words,
        audio_base64=audio_b64,
        source="bedrock",
        is_draft=True,
    )
    await _save_to_redis(redis, payload)
    return payload


async def stream_flashcard(
    keyword: str,
    redis: aioredis.Redis,
    candidate_keywords: list[str] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Format the shared lookup/generation result as existing SSE events."""
    normalized = keyword.lower().strip()
    yield {
        "event": "status",
        "data": {"step": "LOOKUP_CACHE", "message": "Đang tra cứu từ điển và bộ nhớ đệm..."},
    }
    payload = await _lookup(normalized, redis)
    if payload is None:
        yield {
            "event": "status",
            "data": {
                "step": "AI_GENERATING",
                "message": "Đang sinh nội dung và giọng phát âm song song...",
            },
        }
        payload = await _generate_once(normalized, redis)
    yield {
        "event": "vocab_content",
        "data": {
            "step": "VOCAB_CONTENT",
            "source": payload.source,
            "keyword": payload.keyword,
            "pronunciation": payload.pronunciation,
            "meaning_vi": payload.meaning_vi,
            "example_1": payload.example_1,
            "example_2": payload.example_2,
            "related_words": payload.related_words,
            "is_draft": payload.is_draft,
        },
    }
    if payload.audio_base64:
        yield {
            "event": "audio_ready",
            "data": {"step": "AUDIO_READY", "audio_base64": payload.audio_base64},
        }
    yield {
        "event": "done",
        "data": {
            "step": "DONE",
            "status": "SUCCESS",
            "source": payload.source,
            "keyword": normalized,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Confirm Draft – Lưu chính thức vào MongoDB Atlas sau khi user xác nhận
# ─────────────────────────────────────────────────────────────────────────────


async def _sync_existing_to_redis(
    existing: GlobalFlashcard, redis: aioredis.Redis
) -> GlobalFlashcard:
    confirmed = _payload_from_document(existing, source="mongodb", is_draft=False)
    await _save_to_redis(redis, confirmed)
    return existing


async def save_global_flashcard(
    payload: FlashcardPayload,
    redis: aioredis.Redis,
) -> GlobalFlashcard:
    """Lưu thẻ vào DB của bạn (MongoDB Atlas Free M0) + đồng bộ Redis."""
    existing = await GlobalFlashcard.find_one({"keyword": payload.keyword})
    if existing is not None:
        logger.warning("save_global_flashcard: keyword=%s đã tồn tại", payload.keyword)
        return await _sync_existing_to_redis(existing, redis)

    card = GlobalFlashcard(
        keyword=payload.keyword,
        pronunciation=payload.pronunciation,
        meaning_vi=payload.meaning_vi,
        example_1=payload.example_1,
        example_2=payload.example_2,
        related_words=payload.related_words,
        audio_base64=payload.audio_base64,
        embedding=None,
    )

    try:
        await card.insert()
    except DuplicateKeyError:
        # Race: request khác confirm cùng keyword ở giữa lúc find_one() và
        # insert() của mình → người kia thắng, dùng bản ghi của họ.
        logger.info(
            "confirm_persist_race: keyword=%s bị insert trước bởi request khác", payload.keyword
        )
        winner = await GlobalFlashcard.find_one({"keyword": payload.keyword})
        if winner is None:
            # Cực hiếm: bị insert rồi lại bị xóa ngay sau đó — không tự đoán,
            # để caller biết có vấn đề bất thường.
            raise
        return await _sync_existing_to_redis(winner, redis)

    confirmed = _payload_from_document(card, source="mongodb", is_draft=False)
    await _save_to_redis(redis, confirmed)
    logger.info("confirm_persist: keyword=%s đã lưu vào MongoDB Atlas M0 + Redis", payload.keyword)
    return card
