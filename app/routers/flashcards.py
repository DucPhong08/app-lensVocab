from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.exceptions import RedisError

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.flashcard import (
    ConfirmFlashcardRequest,
    FlashcardAudioResponse,
    FlashcardItemResponse,
    FlashcardPageResponse,
)
from app.services.flashcard_service import (
    CardAudioNotFoundError,
    DraftExpiredError,
    confirm_card,
    get_card_audio,
    list_card_page,
    list_cards,
)

router = APIRouter()


@router.post("/flashcards/confirm", response_model=FlashcardItemResponse)
async def confirm_flashcard(
    body: ConfirmFlashcardRequest,
    current_user: User = Depends(get_current_user),
) -> FlashcardItemResponse:
    """
    POST /flashcards/confirm
    Xác nhận thẻ nháp sau khi scan:
      1. Lưu vào GlobalFlashcard (MongoDB Atlas) + đồng bộ Redis.
      2. Tạo bản ghi UserFlashcard cho người dùng với trạng thái CONFIRMED.
    """
    redis = get_redis()
    try:
        return await confirm_card(current_user.id, body, redis)
    except DraftExpiredError as exc:
        raise HTTPException(status_code=409, detail="DRAFT_EXPIRED") from exc
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="REDIS_CONNECTION_FAILED") from exc


@router.get("/flashcards", response_model=list[FlashcardItemResponse])
async def list_user_flashcards(
    current_user: User = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[FlashcardItemResponse]:
    """
    GET /flashcards
    Legacy: trả một trang giới hạn, không kèm âm thanh.
    """
    return await list_cards(current_user.id, limit=limit, offset=offset)


@router.get("/flashcards/page", response_model=FlashcardPageResponse)
async def get_flashcard_page(
    current_user: User = Depends(get_current_user),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> FlashcardPageResponse:
    return await list_card_page(current_user.id, limit=limit, offset=offset)


@router.get("/flashcards/{user_flashcard_id}/audio", response_model=FlashcardAudioResponse)
async def get_flashcard_audio(
    user_flashcard_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
) -> FlashcardAudioResponse:
    try:
        return await get_card_audio(current_user.id, user_flashcard_id)
    except CardAudioNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
