from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from pymongo.errors import DuplicateKeyError

from app.models.flashcard import FlashcardStatus, GlobalFlashcard, UserFlashcard
from app.schemas.flashcard import (
    ConfirmFlashcardRequest,
    FlashcardAudioResponse,
    FlashcardItemResponse,
    FlashcardPageResponse,
    FlashcardText,
    card_fields,
)
from app.services.cache_service import _get_from_redis, save_global_flashcard
from app.services.card_image_service import card_image_urls

if TYPE_CHECKING:
    from redis.asyncio import Redis

_DEFAULT_EFACTOR = 2.5


class DraftExpiredError(Exception):
    pass


async def confirm_card(
    user_id: uuid.UUID,
    data: ConfirmFlashcardRequest,
    redis: Redis,
) -> FlashcardItemResponse:
    """Xác nhận thẻ nháp: lưu GlobalFlashcard + tạo/cập nhật UserFlashcard CONFIRMED."""
    global_card = await GlobalFlashcard.find_one({"keyword": data.keyword})
    if global_card is None:
        payload = await _get_from_redis(redis, data.keyword)
        if payload is None or payload.keyword != data.keyword:
            raise DraftExpiredError("DRAFT_EXPIRED")
        global_card = await save_global_flashcard(payload, redis)

    # 2. Tạo hoặc cập nhật UserFlashcard (dùng dict query để an toàn cả khi test offline)
    user_card = await UserFlashcard.find_one(
        {"user_id": user_id, "global_flashcard_id": global_card.id}
    )

    if user_card is None:
        user_card = UserFlashcard.model_construct(
            id=uuid.uuid4(),
            user_id=user_id,
            global_flashcard_id=global_card.id,
            status=FlashcardStatus.CONFIRMED,
            interval=1,
            repetitions=0,
            efactor=_DEFAULT_EFACTOR,
            next_review_date=date.today(),
            total_reviews=0,
            added_at=datetime.now(UTC),
        )
        try:
            await user_card.insert()
        except DuplicateKeyError:
            user_card = await UserFlashcard.find_one(
                {"user_id": user_id, "global_flashcard_id": global_card.id}
            )
            if user_card is None:
                raise
            await user_card.set({"status": FlashcardStatus.CONFIRMED})
    else:
        await user_card.set({"status": FlashcardStatus.CONFIRMED})

    image_urls = await card_image_urls([user_card])
    return FlashcardItemResponse(
        **card_fields(user_card, global_card, image_urls.get(user_card.id)),
        global_flashcard_id=global_card.id,
        audio_base64=global_card.audio_base64,
        status=user_card.status,
    )


class CardAudioNotFoundError(Exception):
    pass


async def get_card_audio(user_id: uuid.UUID, card_id: uuid.UUID) -> FlashcardAudioResponse:
    owned = await UserFlashcard.find_one({"_id": card_id, "user_id": user_id})
    if owned is None:
        raise CardAudioNotFoundError("FLASHCARD_NOT_FOUND")
    audio = await GlobalFlashcard.find_one({"_id": owned.global_flashcard_id}).project(
        FlashcardAudioResponse
    )
    if audio is None or not audio.audio_base64:
        raise CardAudioNotFoundError("AUDIO_NOT_AVAILABLE")
    return audio


async def list_card_page(
    user_id: uuid.UUID, *, limit: int = 30, offset: int = 0
) -> FlashcardPageResponse:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Invalid pagination bounds")
    query = {"user_id": user_id}
    total = await UserFlashcard.find(query).count()
    user_cards = (
        await UserFlashcard.find(query)
        .sort([("added_at", -1), ("_id", 1)])
        .skip(offset)
        .limit(limit)
        .to_list()
    )
    if not user_cards:
        return FlashcardPageResponse(items=[], total=total, next_offset=None)
    global_ids = [card.global_flashcard_id for card in user_cards]
    global_cards = (
        await GlobalFlashcard.find({"_id": {"$in": global_ids}}).project(FlashcardText).to_list()
    )
    global_map = {card.id: card for card in global_cards}
    image_urls = await card_image_urls(user_cards)
    items = []
    for card in user_cards:
        content = global_map.get(card.global_flashcard_id)
        if content is None:
            continue
        items.append(
            FlashcardItemResponse(
                **card_fields(card, content, image_urls.get(card.id)),
                global_flashcard_id=content.id,
                audio_base64=None,
                status=card.status,
            )
        )
    next_offset = offset + len(user_cards)
    return FlashcardPageResponse(
        items=items, total=total, next_offset=next_offset if next_offset < total else None
    )


async def list_cards(
    user_id: uuid.UUID, *, limit: int = 50, offset: int = 0
) -> list[FlashcardItemResponse]:
    """Bounded legacy array response. New clients use list_card_page."""
    return (await list_card_page(user_id, limit=limit, offset=offset)).items
