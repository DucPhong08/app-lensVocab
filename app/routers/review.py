from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies.auth import get_current_user
from app.models.models import (
    AccountTier,
    FlashcardStatus,
    GlobalFlashcard,
    ReviewLog,
    User,
    UserFlashcard,
)
from app.services.sm2_service import compute_sm2, slice_review_queue
from app.services.system_setting_service import get_system_settings

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────


class ReviewCardResponse(BaseModel):
    user_flashcard_id: uuid.UUID
    keyword: str
    pronunciation: Optional[str]
    meaning_vi: str
    example_1: str
    example_2: str
    related_words: list[str]
    audio_base64: Optional[str]
    interval: int
    repetitions: int
    efactor: float
    next_review_date: str


class SubmitReviewRequest(BaseModel):
    quality: int = Field(
        ..., ge=0, le=5, description="Đánh giá từ 0 (quên hoàn toàn) đến 5 (nhớ hoàn hảo)"
    )


class SubmitReviewResponse(BaseModel):
    user_flashcard_id: uuid.UUID
    interval_after: int
    repetitions_after: int
    efactor_after: float
    next_review_date: str
    message: str


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/review/today", response_model=list[ReviewCardResponse])
async def get_today_review_queue(
    current_user: User = Depends(get_current_user),
) -> list[ReviewCardResponse]:
    """
    GET /review/today
    Lấy danh sách từ cần ôn hôm nay:
      - next_review_date <= hôm nay.
      - Giới hạn tối đa 15 từ (Anti-demotivation cap) để người mất gốc không bị quá tải.
    """
    today = date.today()

    due_cards = (
        await UserFlashcard.find(
            UserFlashcard.user_id == current_user.id,
            UserFlashcard.status == FlashcardStatus.CONFIRMED,
            UserFlashcard.next_review_date <= today,
        )
        .sort("next_review_date")
        .to_list()
    )

    # Cắt hàng đợi theo mục tiêu của user và trần quy định của gói cước
    sys_settings = await get_system_settings()
    user_goal = current_user.preferences.daily_review_goal if current_user.preferences else 15
    if current_user.account_tier == AccountTier.PREMIUM:
        effective_cap = min(user_goal, sys_settings.premium_daily_review_cap)
    else:
        effective_cap = min(user_goal, sys_settings.free_daily_review_cap)

    sliced_cards = slice_review_queue(due_cards, cap=effective_cap)
    if not sliced_cards:
        return []

    global_ids = [card.global_flashcard_id for card in sliced_cards]
    global_cards = await GlobalFlashcard.find({"_id": {"$in": global_ids}}).to_list()
    global_map = {gc.id: gc for gc in global_cards}

    result: list[ReviewCardResponse] = []
    for uc in sliced_cards:
        gc = global_map.get(uc.global_flashcard_id)
        if not gc:
            continue
        result.append(
            ReviewCardResponse(
                user_flashcard_id=uc.id,
                keyword=gc.keyword,
                pronunciation=gc.pronunciation,
                meaning_vi=gc.meaning_vi,
                example_1=gc.example_1,
                example_2=gc.example_2,
                related_words=gc.related_words,
                audio_base64=gc.audio_base64,
                interval=uc.interval,
                repetitions=uc.repetitions,
                efactor=uc.efactor,
                next_review_date=uc.next_review_date.isoformat(),
            )
        )

    return result


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
    card = await UserFlashcard.get(user_flashcard_id)
    if not card or card.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="FLASHCARD_NOT_FOUND",
        )

    interval_before = card.interval
    efactor_before = card.efactor

    # 1. Tính toán thuật toán SM-2
    sm2_res = compute_sm2(
        quality=body.quality,
        interval=card.interval,
        repetitions=card.repetitions,
        efactor=card.efactor,
    )

    # 2. Cập nhật UserFlashcard
    card.interval = sm2_res.interval
    card.repetitions = sm2_res.repetitions
    card.efactor = sm2_res.efactor
    card.next_review_date = sm2_res.next_review_date
    card.total_reviews += 1
    card.last_reviewed_at = datetime.now(UTC)
    await card.save()

    # 3. Ghi log ôn tập (Audit & Analytics)
    log = ReviewLog(
        user_id=card.user_id,
        user_flashcard_id=card.id,
        quality=body.quality,
        interval_before=interval_before,
        interval_after=sm2_res.interval,
        efactor_before=efactor_before,
        efactor_after=sm2_res.efactor,
    )
    await log.insert()

    msg = (
        "Ôn tập thành công!"
        if body.quality >= 3
        else "Đã ghi nhận, từ này sẽ xuất hiện lại vào ngày mai!"
    )

    return SubmitReviewResponse(
        user_flashcard_id=card.id,
        interval_after=sm2_res.interval,
        repetitions_after=sm2_res.repetitions,
        efactor_after=sm2_res.efactor,
        next_review_date=sm2_res.next_review_date.isoformat(),
        message=msg,
    )
