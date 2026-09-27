from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.flashcard import ConfirmFlashcardRequest, FlashcardItemResponse
from app.services.flashcard_service import confirm_card, list_cards

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
    return await confirm_card(current_user.id, body, redis)


@router.get("/flashcards", response_model=list[FlashcardItemResponse])
async def list_user_flashcards(
    current_user: User = Depends(get_current_user),
) -> list[FlashcardItemResponse]:
    """
    GET /flashcards
    Lấy danh sách tất cả các flashcard của người dùng hiện tại.
    """
    return await list_cards(current_user.id)
