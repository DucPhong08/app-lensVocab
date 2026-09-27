from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from app.models.flashcard import FlashcardStatus, GlobalFlashcard, UserFlashcard
from app.models.review import ReviewLog
from app.models.user import AccountTier, User
from app.schemas.review import ReviewCardResponse, SubmitReviewResponse
from app.services.sm2_service import compute_sm2, slice_review_queue
from app.services.system_setting_service import get_system_settings


class FlashcardNotFoundError(Exception):
    """Không tìm thấy thẻ hoặc không thuộc sở hữu của người dùng."""

    pass


async def get_today_queue(current_user: User) -> list[ReviewCardResponse]:
    """Lấy danh sách các thẻ cần ôn hôm nay theo thuật toán SM-2 và trần chống nản."""
    today = date.today()

    due_cards = (
        await UserFlashcard.find(
            {
                "user_id": current_user.id,
                "status": FlashcardStatus.CONFIRMED,
                "next_review_date": {"$lte": today},
            }
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


async def submit_review(
    user_id: uuid.UUID,
    user_flashcard_id: uuid.UUID,
    quality: int,
) -> SubmitReviewResponse:
    """Chấm điểm ôn tập (SM-2 Spaced Repetition) và ghi nhật ký ReviewLog."""
    card = await UserFlashcard.get(user_flashcard_id)
    if not card or card.user_id != user_id:
        raise FlashcardNotFoundError("FLASHCARD_NOT_FOUND")

    interval_before = card.interval
    efactor_before = card.efactor

    # 1. Tính toán thuật toán SM-2
    sm2_res = compute_sm2(
        quality=quality,
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

    # 3. Ghi log ôn tập (dùng model_construct để an toàn khi test offline)
    log = ReviewLog.model_construct(
        id=uuid.uuid4(),
        user_id=card.user_id,
        user_flashcard_id=card.id,
        quality=quality,
        interval_before=interval_before,
        interval_after=sm2_res.interval,
        efactor_before=efactor_before,
        efactor_after=sm2_res.efactor,
        reviewed_at=datetime.now(UTC),
    )
    await log.insert()

    msg = (
        "Ôn tập thành công!"
        if quality >= 3
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
