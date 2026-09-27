from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from app.dependencies.auth import get_current_user
from app.models.models import FlashcardStatus, GlobalFlashcard, User, UserFlashcard
from app.redis_client import get_redis
from app.services.cache_service import FlashcardPayload, save_global_flashcard

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────


class ConfirmFlashcardRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "keyword": "chair",
                "pronunciation": "/tʃer/",
                "meaning_vi": "cái ghế",
                "example_1": "I sit on a chair.",
                "example_2": "This wooden chair is sturdy.",
                "related_words": ["seat", "sofa", "stool"],
                "audio_base64": None,
            }
        }
    }

    keyword: str
    pronunciation: Optional[str] = None
    meaning_vi: str
    example_1: str
    example_2: str
    related_words: list[str] = []
    audio_base64: Optional[str] = None


class FlashcardItemResponse(BaseModel):
    user_flashcard_id: uuid.UUID
    global_flashcard_id: uuid.UUID
    keyword: str
    pronunciation: Optional[str]
    meaning_vi: str
    example_1: str
    example_2: str
    related_words: list[str]
    audio_base64: Optional[str]
    status: FlashcardStatus
    interval: int
    repetitions: int
    efactor: float
    next_review_date: str


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/flashcards/confirm", response_model=FlashcardItemResponse)
async def confirm_flashcard(
    body: ConfirmFlashcardRequest,
    current_user: User = Depends(get_current_user),
) -> FlashcardItemResponse:
    """
    POST /flashcards/confirm
    Xác nhận thẻ nháp sau khi scan:
      1. Lưu vào GlobalFlashcard (MongoDB Atlas M0) + đồng bộ Redis.
      2. Tạo bản ghi UserFlashcard cho người dùng với trạng thái CONFIRMED.
    """
    redis = get_redis()

    payload = FlashcardPayload(
        keyword=body.keyword,
        pronunciation=body.pronunciation,
        meaning_vi=body.meaning_vi,
        example_1=body.example_1,
        example_2=body.example_2,
        related_words=body.related_words,
        audio_base64=body.audio_base64,
        source="bedrock",
        is_draft=False,
    )

    # 1. Lưu GlobalFlashcard
    global_card = await save_global_flashcard(payload, redis)

    # 2. Tạo hoặc lấy UserFlashcard
    user_card = await UserFlashcard.find_one(
        UserFlashcard.user_id == current_user.id,
        UserFlashcard.global_flashcard_id == global_card.id,
    )

    if user_card is None:
        user_card = UserFlashcard(
            user_id=current_user.id,
            global_flashcard_id=global_card.id,
            status=FlashcardStatus.CONFIRMED,
        )
        try:
            await user_card.insert()
        except DuplicateKeyError:
            # Race condition: bản ghi vừa được tạo bởi request đồng thời
            user_card = await UserFlashcard.find_one(
                UserFlashcard.user_id == current_user.id,
                UserFlashcard.global_flashcard_id == global_card.id,
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


@router.get("/flashcards", response_model=list[FlashcardItemResponse])
async def list_user_flashcards(
    current_user: User = Depends(get_current_user),
) -> list[FlashcardItemResponse]:
    """
    GET /flashcards
    Lấy danh sách tất cả các flashcard của người dùng hiện tại.
    """
    user_cards = await UserFlashcard.find(UserFlashcard.user_id == current_user.id).to_list()
    if not user_cards:
        return []

    global_ids = [uc.global_flashcard_id for uc in user_cards]
    global_cards = await GlobalFlashcard.find({"_id": {"$in": global_ids}}).to_list()
    global_map = {gc.id: gc for gc in global_cards}

    response_list: list[FlashcardItemResponse] = []
    for uc in user_cards:
        gc = global_map.get(uc.global_flashcard_id)
        if not gc:
            continue
        response_list.append(
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

    return response_list
