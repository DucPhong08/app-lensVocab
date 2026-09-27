from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any

import redis.asyncio as aioredis
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.config import settings
from app.models.models import GlobalFlashcard
from app.services.ai_service import (
    create_titan_embedding,
    generate_flashcard_content,
    synthesize_speech,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data Transfer Object
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FlashcardPayload:
    keyword: str
    pronunciation: str | None           # /tʃer/
    meaning_vi: str                     # cái ghế
    example_1: str                      # I sit on a chair.
    example_2: str                      # This chair is comfortable.
    related_words: list[str]            # ["seat", "sofa", "stool"]
    audio_base64: str | None            # MP3 audio từ AWS Polly
    source: str                         # "redis" | "mongodb" | "bedrock"
    is_draft: bool                      # True nếu từ Tầng 3 (chờ user xác nhận)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _payload_from_document(doc: GlobalFlashcard, *, source: str, is_draft: bool) -> FlashcardPayload:
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
# Tầng 2 – MongoDB Atlas (Exact Match rồi mới tới Vector Search)
# ─────────────────────────────────────────────────────────────────────────────

async def _find_exact_mongodb(keyword: str) -> GlobalFlashcard | None:
    """Exact match theo keyword — rẻ hơn vector search rất nhiều (không tốn Titan
    embedding), nên luôn thử trước. Bắt trường hợp Redis miss nhưng Mongo vẫn còn
    record cũ (vd TTL Redis hết hạn)."""
    return await GlobalFlashcard.find_one(GlobalFlashcard.keyword == keyword)


async def _search_mongodb_vector(embedding: list[float]) -> GlobalFlashcard | None:
    """
    Tìm từ tương đồng ngữ nghĩa bằng $vectorSearch aggregation của MongoDB Atlas.
    Vector dimension: 1024 (Amazon Titan Embeddings v2).
    """
    pipeline = [
        {
            "$vectorSearch": {
                "index": settings.VECTOR_INDEX_NAME,
                "path": "embedding",
                "queryVector": embedding,
                "numCandidates": 20,
                "limit": 1,
            }
        },
        {
            "$addFields": {
                "score": {"$meta": "vectorSearchScore"},
                "id": "$_id",
            }
        },
        {
            "$match": {
                "score": {"$gte": settings.SEMANTIC_SIMILARITY_THRESHOLD}
            }
        },
    ]

    try:
        results = await GlobalFlashcard.aggregate(pipeline).to_list(length=1)
    except OperationFailure as exc:
        # Case mong đợi: index vector chưa tạo (vd dev local chưa config Atlas
        # Search). An toàn để bỏ qua sang Tầng 3.
        logger.warning("vector_search_index_missing: %s", exc)
        return None
    except Exception as exc:
        # Case KHÔNG mong đợi (Mongo down, network...) — log ở mức error để có
        # alert riêng, khác với case index-chưa-tạo ở trên. Vẫn return None để
        # user không bị vỡ luồng, nhưng phải nhìn thấy được trong monitoring.
        logger.error("vector_search_unexpected_failure: %s", exc)
        return None

    if not results:
        return None
    return GlobalFlashcard.model_validate(results[0])


# ─────────────────────────────────────────────────────────────────────────────
# Public API – Multi-Tier Cache Resolver (Pure AWS)
# ─────────────────────────────────────────────────────────────────────────────

async def resolve_flashcard(
    keyword: str,
    redis: aioredis.Redis,
) -> FlashcardPayload:
    """
    Luồng 3 tầng:
      Tầng 1  → Redis exact match                    (cost = $0)
      Tầng 2a → MongoDB exact match                   (cost = $0)
      Tầng 2b → MongoDB Atlas Vector Search           (cost = Bedrock Titan embedding)
      Tầng 3  → AWS Bedrock (Nova) + Polly             (cost = Bedrock inference + Polly TTS)
    """
    normalized = keyword.lower().strip()

    # ── Tầng 1: Redis ─────────────────────────────────────────────────────────
    cached = await _get_from_redis(redis, normalized)
    if cached is not None:
        logger.info("cache_hit=redis keyword=%s", normalized)
        return cached

    # ── Tầng 2a: MongoDB exact match ─────────────────────────────────────────
    exact = await _find_exact_mongodb(normalized)
    if exact is not None:
        logger.info("cache_hit=mongodb_exact keyword=%s", normalized)
        payload = _payload_from_document(exact, source="mongodb", is_draft=False)
        await _save_to_redis(redis, payload)
        return payload

    # ── Tầng 2b: MongoDB Vector Search ───────────────────────────────────────
    match = None
    try:
        embedding = await create_titan_embedding(normalized)
    except Exception as exc:
        logger.warning("titan_embedding_failed: %s", exc)
        embedding = None

    if embedding is not None:
        match = await _search_mongodb_vector(embedding)

    if match is not None:
        logger.info("cache_hit=mongodb_vector keyword=%s matched=%s", normalized, match.keyword)
        payload = _payload_from_document(match, source="mongodb", is_draft=False)
        await _save_to_redis(redis, payload)
        return payload

    # ── Tầng 3: AWS Bedrock (Nova) + AWS Polly ────────────────────────────────
    logger.info("cache_miss=all keyword=%s → gọi AWS Bedrock + Polly", normalized)
    generated = await generate_flashcard_content(normalized)

    # Sinh giọng đọc bằng AWS Polly
    try:
        audio_b64 = await synthesize_speech(normalized)
    except Exception as exc:
        logger.error("polly_tts_failed: %s", exc)
        audio_b64 = None

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
    # Cache draft luôn — tránh gọi lại Bedrock/Polly nếu cùng keyword được lookup
    # lần nữa trước khi user confirm (reload trang, 2 user khác nhau...).
    await _save_to_redis(redis, payload)
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Confirm Draft – Lưu chính thức vào MongoDB Atlas sau khi user xác nhận
# ─────────────────────────────────────────────────────────────────────────────

async def _persist_existing(existing: GlobalFlashcard, redis: aioredis.Redis) -> GlobalFlashcard:
    confirmed = _payload_from_document(existing, source="mongodb", is_draft=False)
    await _save_to_redis(redis, confirmed)
    return existing


async def confirm_and_persist(
    payload: FlashcardPayload,
    redis: aioredis.Redis,
) -> GlobalFlashcard:
    """Lưu thẻ vào DB của bạn (MongoDB Atlas Free M0) + đồng bộ Redis."""
    existing = await GlobalFlashcard.find_one(GlobalFlashcard.keyword == payload.keyword)
    if existing is not None:
        logger.warning("confirm_persist: keyword=%s đã tồn tại", payload.keyword)
        return await _persist_existing(existing, redis)

    try:
        embedding = await create_titan_embedding(payload.keyword)
    except Exception as exc:
        logger.warning("create_embedding_on_persist_failed: %s", exc)
        embedding = None

    card = GlobalFlashcard(
        keyword=payload.keyword,
        pronunciation=payload.pronunciation,
        meaning_vi=payload.meaning_vi,
        example_1=payload.example_1,
        example_2=payload.example_2,
        related_words=payload.related_words,
        audio_base64=payload.audio_base64,
        embedding=embedding,
    )

    try:
        await card.insert()
    except DuplicateKeyError:
        # Race: request khác confirm cùng keyword ở giữa lúc find_one() và
        # insert() của mình → người kia thắng, dùng bản ghi của họ.
        logger.info("confirm_persist_race: keyword=%s bị insert trước bởi request khác", payload.keyword)
        winner = await GlobalFlashcard.find_one(GlobalFlashcard.keyword == payload.keyword)
        if winner is None:
            # Cực hiếm: bị insert rồi lại bị xóa ngay sau đó — không tự đoán,
            # để caller biết có vấn đề bất thường.
            raise
        return await _persist_existing(winner, redis)

    confirmed = _payload_from_document(card, source="mongodb", is_draft=False)
    await _save_to_redis(redis, confirmed)
    logger.info("confirm_persist: keyword=%s đã lưu vào MongoDB Atlas M0 + Redis", payload.keyword)
    return card