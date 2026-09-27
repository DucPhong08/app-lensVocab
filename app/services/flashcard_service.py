from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from pymongo.errors import DuplicateKeyError

from app.models.flashcard import FlashcardStatus, GlobalFlashcard, UserFlashcard
from app.schemas.flashcard import ConfirmFlashcardRequest, FlashcardItemResponse
from app.services.cache_service import FlashcardPayload, save_global_flashcard

if TYPE_CHECKING:
    from redis.asyncio import Redis

_DEFAULT_EFACTOR = 2.5


async def confirm_card(
    user_id: uuid.UUID,
    data: ConfirmFlashcardRequest,
    redis: Redis,
) -> FlashcardItemResponse:
    """Xác nhận thẻ nháp: lưu GlobalFlashcard + tạo/cập nhật UserFlashcard CONFIRMED."""
    payload = FlashcardPayload(
        keyword=data.keyword,
        pronunciation=data.pronunciation,
        meaning_vi=data.meaning_vi,
        example_1=data.example_1,
        example_2=data.example_2,
        related_words=data.related_words,
        audio_base64=data.audio_base64,
        source="bedrock",
        is_draft=False,
    )

    # 1. Lưu GlobalFlashcard
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
            if user_card:
                user_card.status = FlashcardStatus.CONFIRMED
                await user_card.save()
    else:
        user_card.status = FlashcardStatus.CONFIRMED
        await user_card.save()

    return FlashcardItemResponse(
        user_flashcard_id=user_card.id,
        global_flashcard_id=global_card.id,
        keyword=global_card.keyword,
        pronunciation=global_card.pronunciation,
        meaning_vi=global_card.meaning_vi,
        example_1=global_card.example_1,
        example_2=global_card.example_2,
        related_words=global_card.related_words,
        audio_base64=global_card.audio_base64,
        status=user_card.status,
        interval=user_card.interval,
        repetitions=user_card.repetitions,
        efactor=user_card.efactor,
        next_review_date=user_card.next_review_date.isoformat(),
    )


async def list_cards(user_id: uuid.UUID) -> list[FlashcardItemResponse]:
    """Lấy danh sách tất cả các flashcard của người dùng."""
    user_cards = await UserFlashcard.find({"user_id": user_id}).to_list()
    if not user_cards:
        return []

    global_ids = [uc.global_flashcard_id for uc in user_cards]
    global_cards = await GlobalFlashcard.find({"_id": {"$in": global_ids}}).to_list()
    global_map = {gc.id: gc for gc in global_cards}

    result: list[FlashcardItemResponse] = []
    for uc in user_cards:
        gc = global_map.get(uc.global_flashcard_id)
        if not gc:
            continue
        result.append(
            FlashcardItemResponse(
                user_flashcard_id=uc.id,
                global_flashcard_id=gc.id,
                keyword=gc.keyword,
                pronunciation=gc.pronunciation,
                meaning_vi=gc.meaning_vi,
                example_1=gc.example_1,
                example_2=gc.example_2,
                related_words=gc.related_words,
                audio_base64=gc.audio_base64,
                status=uc.status,
                interval=uc.interval,
                repetitions=uc.repetitions,
                efactor=uc.efactor,
                next_review_date=uc.next_review_date.isoformat(),
            )
        )

    return result
