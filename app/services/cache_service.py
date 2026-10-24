from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass
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

    # ── Cache Miss: AWS Bedrock Nova Lite + AWS Polly (song song) ───────────────
    # Bedrock and Polly are independent; no fixed latency improvement is assumed.
    logger.info("cache_miss=all keyword=%s → gọi AWS Bedrock + Polly song song", normalized)

    generated, audio_b64 = await asyncio.gather(
        generate_flashcard_content(normalized),
        _safe_synthesize(normalized),
    )

    payload = FlashcardPayload(
        keyword=normalized,
        pronunciation=generated.pronunciation,
        meaning_vi=generated.meaning_vi,
        example_1=generated.example_1,
        example_2=generated.example_2,
        related_words=generated.related_words,
        audio_base64=audio_b64,
        source="bedrock",
        is_draft=True,
    )
    # Cache draft ngay — tránh gọi lại Bedrock/Polly nếu cùng keyword được lookup
    # lần nữa trước khi user confirm (reload trang, 2 user khác nhau...).
    await _save_to_redis(redis, payload)
    return payload


async def stream_flashcard(
    keyword: str,
    redis: aioredis.Redis,
    candidate_keywords: list[str] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream exact-keyword cache/generation progress, without parent substitution."""
    normalized = keyword.lower().strip()

    yield {
        "event": "status",
        "data": {"step": "LOOKUP_CACHE", "message": "Đang tra cứu từ điển và bộ nhớ đệm..."},
    }

    # ── Tầng 1: Redis Exact Match (keyword chính) ──────────────────────────────
    cached = await _get_from_redis(redis, normalized)
    if cached is not None:
        logger.info("cache_hit=redis keyword=%s", normalized)
        yield {
            "event": "vocab_content",
            "data": {
                "step": "VOCAB_CONTENT",
                "source": "redis",
                "keyword": cached.keyword,
                "pronunciation": cached.pronunciation,
                "meaning_vi": cached.meaning_vi,
                "example_1": cached.example_1,
                "example_2": cached.example_2,
                "related_words": cached.related_words,
                "is_draft": cached.is_draft,
            },
        }
        if cached.audio_base64:
            yield {
                "event": "audio_ready",
                "data": {"step": "AUDIO_READY", "audio_base64": cached.audio_base64},
            }
        yield {
            "event": "done",
            "data": {"step": "DONE", "status": "SUCCESS", "source": "redis", "keyword": normalized},
        }
        return

    # ── Tầng 2: MongoDB Exact Match (keyword chính) ───────────────────────────
    exact = await _find_exact_mongodb(normalized)
    if exact is not None:
        logger.info("cache_hit=mongodb_exact keyword=%s", normalized)
        payload = _payload_from_document(exact, source="mongodb", is_draft=False)
        await _save_to_redis(redis, payload)
        yield {
            "event": "vocab_content",
            "data": {
                "step": "VOCAB_CONTENT",
                "source": "mongodb",
                "keyword": payload.keyword,
                "pronunciation": payload.pronunciation,
                "meaning_vi": payload.meaning_vi,
                "example_1": payload.example_1,
                "example_2": payload.example_2,
                "related_words": payload.related_words,
                "is_draft": False,
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
                "source": "mongodb",
                "keyword": normalized,
            },
        }
        return

    # ── Cache Miss: AWS Bedrock Nova Lite + AWS Polly (song song) ───────────────
    # Chạy Bedrock và Polly đồng thời — hai tác vụ độc lập, không cần đợi nhau.
    logger.info("cache_miss=all keyword=%s → gọi AWS Bedrock + Polly song song", normalized)
    yield {
        "event": "status",
        "data": {
            "step": "AI_GENERATING",
            "message": "Đang sinh nội dung và giọng phát âm song song...",
        },
    }

    generated, audio_b64 = await asyncio.gather(
        generate_flashcard_content(normalized),
        _safe_synthesize(normalized),
    )

    yield {
        "event": "vocab_content",
        "data": {
            "step": "VOCAB_CONTENT",
            "source": "bedrock",
            "keyword": normalized,
            "pronunciation": generated.pronunciation,
            "meaning_vi": generated.meaning_vi,
            "example_1": generated.example_1,
            "example_2": generated.example_2,
            "related_words": generated.related_words,
            "is_draft": True,
        },
    }

    if audio_b64:
        yield {
            "event": "audio_ready",
            "data": {"step": "AUDIO_READY", "audio_base64": audio_b64},
        }

    payload = FlashcardPayload(
        keyword=normalized,
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

    yield {
        "event": "done",
        "data": {
            "step": "DONE",
            "status": "SUCCESS",
            "source": "bedrock",
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
