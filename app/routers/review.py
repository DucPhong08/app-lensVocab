from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.review import ReviewCardResponse, SubmitReviewRequest, SubmitReviewResponse
from app.services.review_service import FlashcardNotFoundError, get_today_queue, submit_review

router = APIRouter()


@router.get("/review/today", response_model=list[ReviewCardResponse])
async def get_today_review_queue(
    current_user: User = Depends(get_current_user),
) -> list[ReviewCardResponse]:
    """
    GET /review/today
    Lấy danh sách từ cần ôn hôm nay:
      - next_review_date <= hôm nay.
      - Giới hạn tối đa theo mục tiêu cá nhân và trần gói cước (Anti-demotivation cap).
    """
    return await get_today_queue(current_user)


@router.post("/review/{user_flashcard_id}", response_model=SubmitReviewResponse)
async def submit_card_review(
    user_flashcard_id: uuid.UUID,
    body: SubmitReviewRequest,
    current_user: User = Depends(get_current_user),
) -> SubmitReviewResponse:
    """
    POST /review/{user_flashcard_id}
    Chấm điểm ôn tập (SM-2 Spaced Repetition):
      - quality: 0-2 (quên) → reset interval về 1 ngày.
      - quality: 3-5 (nhớ) → tăng interval theo efactor.
    """
    try:
        return await submit_review(current_user.id, user_flashcard_id, body.quality)
    except FlashcardNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="FLASHCARD_NOT_FOUND",
        )
