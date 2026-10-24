from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from pymongo.errors import DuplicateKeyError

from app.database import get_motor_client
from app.models.flashcard import FlashcardStatus, GlobalFlashcard, UserFlashcard
from app.models.review import ReviewLog
from app.models.user import AccountTier, User
from app.schemas.flashcard import FlashcardText
from app.schemas.review import ReviewCardResponse, SubmitReviewResponse
from app.services.card_image_service import card_image_urls
from app.services.sm2_service import compute_sm2
from app.services.system_setting_service import fetch_system_settings


class FlashcardNotFoundError(Exception):
    """Không tìm thấy thẻ hoặc không thuộc sở hữu của người dùng."""

    pass


async def fetch_today_queue(current_user: User) -> list[ReviewCardResponse]:
    """Lấy danh sách các thẻ cần ôn hôm nay theo thuật toán SM-2 và trần chống nản."""
    today = date.today()

    # Cắt hàng đợi theo mục tiêu của user và trần quy định của gói cước
    sys_settings = await fetch_system_settings()
    user_goal = current_user.preferences.daily_review_goal if current_user.preferences else 15
    if current_user.account_tier == AccountTier.PREMIUM:
        effective_cap = min(user_goal, sys_settings.premium_daily_review_cap)
    else:
        effective_cap = min(user_goal, sys_settings.free_daily_review_cap)

    if effective_cap <= 0:
        return []

    sliced_cards = (
        await UserFlashcard.find(
            {
                "user_id": current_user.id,
                "status": FlashcardStatus.CONFIRMED,
                "next_review_date": {"$lte": today},
            }
        )
        .sort("next_review_date")
        .limit(effective_cap)
        .to_list()
    )
    if not sliced_cards:
        return []

    global_ids = [card.global_flashcard_id for card in sliced_cards]
    global_cards = (
        await GlobalFlashcard.find({"_id": {"$in": global_ids}}).project(FlashcardText).to_list()
    )
    global_map = {gc.id: gc for gc in global_cards}
    image_urls = await card_image_urls(sliced_cards)

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
                audio_base64=None,
                image_url=image_urls.get(uc.id),
                has_image=bool(uc.image_key),
                image_style="sticker" if (uc.image_key or "").endswith(".png") else "photo",
                interval=uc.interval,
                repetitions=uc.repetitions,
                efactor=uc.efactor,
                next_review_date=uc.next_review_date.isoformat(),
            )
        )

    return result


class ReviewConflictError(Exception):
    pass


async def submit_review(
    user_id: uuid.UUID,
    user_flashcard_id: uuid.UUID,
    quality: int,
    review_id: uuid.UUID | None = None,
) -> SubmitReviewResponse:
    """Commit card + log together; replays return the original grading result."""
    query = {"user_id": user_id, "review_id": review_id}

    def replay(log: ReviewLog) -> SubmitReviewResponse:
        if (
            log.user_flashcard_id != user_flashcard_id
            or log.quality != quality
            or log.result is None
        ):
            raise ReviewConflictError("REVIEW_ID_CONFLICT")
        return SubmitReviewResponse.model_validate(log.result)

    async def grade(session) -> SubmitReviewResponse:
        if review_id is not None:
            previous = await ReviewLog.find_one(query, session=session)
            if previous is not None:
                return replay(previous)
        card = await UserFlashcard.get(user_flashcard_id, session=session)
        if not card or card.user_id != user_id:
            raise FlashcardNotFoundError("FLASHCARD_NOT_FOUND")

        result = compute_sm2(
            quality=quality,
            interval=card.interval,
            repetitions=card.repetitions,
            efactor=card.efactor,
        )
        response = SubmitReviewResponse(
            user_flashcard_id=card.id,
            interval_after=result.interval,
            repetitions_after=result.repetitions,
            efactor_after=result.efactor,
            next_review_date=result.next_review_date.isoformat(),
            message="Ôn tập thành công!"
            if quality >= 3
            else "Đã ghi nhận, từ này sẽ xuất hiện lại vào ngày mai!",
        )
        log = ReviewLog.model_construct(
            id=uuid.uuid4(),
            user_id=user_id,
            user_flashcard_id=card.id,
            review_id=review_id,
            result=response.model_dump(mode="json"),
            quality=quality,
            interval_before=card.interval,
            interval_after=result.interval,
            efactor_before=card.efactor,
            efactor_after=result.efactor,
            reviewed_at=datetime.now(UTC),
        )
        card.interval = result.interval
        card.repetitions = result.repetitions
        card.efactor = result.efactor
        card.next_review_date = result.next_review_date
        card.total_reviews += 1
        card.last_reviewed_at = log.reviewed_at
        await card.save(session=session)
        await log.insert(session=session)
        return response

    async with await get_motor_client().start_session() as session:
        try:
            return await session.with_transaction(grade)
        except DuplicateKeyError:
            # A concurrent replay may win the unique review ID after our snapshot.
            if review_id is not None:
                previous = await ReviewLog.find_one(query)
                if previous is not None:
                    return replay(previous)
            raise
