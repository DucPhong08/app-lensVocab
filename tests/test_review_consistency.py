from __future__ import annotations

import unittest
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.models.flashcard import FlashcardStatus, GlobalFlashcard, UserFlashcard
from app.models.review import ReviewLog
from app.models.user import AccountTier, User, UserPreferences
from app.routers.review import submit_card_review
from app.schemas.review import SubmitReviewRequest, SubmitReviewResponse
from app.services.review_service import (
    ReviewConflictError,
    fetch_today_queue,
    submit_review,
)


class TestReviewConsistency(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.card_id = uuid.uuid4()
        self.review_id = uuid.uuid4()
        self.session = MagicMock()
        self.session.__aenter__ = AsyncMock(return_value=self.session)
        self.session.__aexit__ = AsyncMock(return_value=False)

        async def run_transaction(callback):
            return await callback(self.session)

        self.session.with_transaction = AsyncMock(side_effect=run_transaction)
        client = MagicMock()
        client.start_session = AsyncMock(return_value=self.session)
        self.client_patcher = patch(
            "app.services.review_service.get_motor_client", return_value=client
        )
        self.client_patcher.start()
        self.addCleanup(self.client_patcher.stop)

    def make_card(self) -> UserFlashcard:
        return UserFlashcard.model_construct(
            id=self.card_id,
            user_id=self.user_id,
            global_flashcard_id=uuid.uuid4(),
            status=FlashcardStatus.CONFIRMED,
            interval=1,
            repetitions=0,
            efactor=2.5,
            total_reviews=0,
        )

    def make_response(self) -> SubmitReviewResponse:
        return SubmitReviewResponse(
            user_flashcard_id=self.card_id,
            interval_after=1,
            repetitions_after=1,
            efactor_after=2.5,
            next_review_date="2026-10-12",
            message="Ôn tập thành công!",
        )

    async def test_card_and_log_writes_share_transaction_session(self):
        card = self.make_card()
        with (
            patch.object(
                ReviewLog, "find_one", new_callable=AsyncMock, return_value=None
            ) as find_log,
            patch.object(
                UserFlashcard, "get", new_callable=AsyncMock, return_value=card
            ) as get_card,
            patch.object(UserFlashcard, "save", new_callable=AsyncMock) as save_card,
            patch.object(
                ReviewLog, "model_construct", wraps=ReviewLog.model_construct
            ) as construct_log,
            patch.object(ReviewLog, "insert", new_callable=AsyncMock) as insert_log,
        ):
            result = await submit_review(
                self.user_id, self.card_id, quality=4, review_id=self.review_id
            )

        self.session.with_transaction.assert_awaited_once()
        find_log.assert_awaited_once_with(
            {"user_id": self.user_id, "review_id": self.review_id},
            session=self.session,
        )
        get_card.assert_awaited_once_with(self.card_id, session=self.session)
        self.assertIs(save_card.await_args.kwargs["session"], self.session)
        self.assertIs(insert_log.await_args.kwargs["session"], self.session)
        self.assertEqual(construct_log.call_args.kwargs["review_id"], self.review_id)
        self.assertEqual(construct_log.call_args.kwargs["result"], result.model_dump(mode="json"))
        self.assertEqual(card.total_reviews, 1)

    async def test_log_failure_escapes_transaction_instead_of_returning_success(self):
        card = self.make_card()
        with (
            patch.object(ReviewLog, "find_one", new_callable=AsyncMock, return_value=None),
            patch.object(UserFlashcard, "get", new_callable=AsyncMock, return_value=card),
            patch.object(UserFlashcard, "save", new_callable=AsyncMock) as save_card,
            patch.object(
                ReviewLog,
                "insert",
                new_callable=AsyncMock,
                side_effect=RuntimeError("log write failed"),
            ) as insert_log,
        ):
            with self.assertRaisesRegex(RuntimeError, "log write failed"):
                await submit_review(self.user_id, self.card_id, quality=4, review_id=self.review_id)

        self.session.with_transaction.assert_awaited_once()
        save_card.assert_awaited_once()
        insert_log.assert_awaited_once()

    async def test_same_review_id_replays_saved_response_without_grading_again(self):
        response = self.make_response()
        previous = ReviewLog.model_construct(
            user_id=self.user_id,
            user_flashcard_id=self.card_id,
            review_id=self.review_id,
            quality=4,
            result=response.model_dump(mode="json"),
        )
        with (
            patch.object(ReviewLog, "find_one", new_callable=AsyncMock, return_value=previous),
            patch.object(UserFlashcard, "get", new_callable=AsyncMock) as get_card,
            patch.object(UserFlashcard, "save", new_callable=AsyncMock) as save_card,
            patch.object(ReviewLog, "insert", new_callable=AsyncMock) as insert_log,
        ):
            replayed = await submit_review(
                self.user_id, self.card_id, quality=4, review_id=self.review_id
            )

        self.assertEqual(replayed, response)
        get_card.assert_not_awaited()
        save_card.assert_not_awaited()
        insert_log.assert_not_awaited()

    async def test_reused_review_id_with_changed_answer_is_conflict(self):
        previous = ReviewLog.model_construct(
            user_id=self.user_id,
            user_flashcard_id=self.card_id,
            review_id=self.review_id,
            quality=4,
            result=self.make_response().model_dump(mode="json"),
        )
        with (
            patch.object(ReviewLog, "find_one", new_callable=AsyncMock, return_value=previous),
            patch.object(UserFlashcard, "get", new_callable=AsyncMock) as get_card,
        ):
            with self.assertRaisesRegex(ReviewConflictError, "REVIEW_ID_CONFLICT"):
                await submit_review(self.user_id, self.card_id, quality=2, review_id=self.review_id)

        get_card.assert_not_awaited()

    async def test_reused_review_id_for_different_card_is_conflict(self):
        previous = ReviewLog.model_construct(
            user_id=self.user_id,
            user_flashcard_id=self.card_id,
            review_id=self.review_id,
            quality=4,
            result=self.make_response().model_dump(mode="json"),
        )
        with patch.object(ReviewLog, "find_one", new_callable=AsyncMock, return_value=previous):
            with self.assertRaisesRegex(ReviewConflictError, "REVIEW_ID_CONFLICT"):
                await submit_review(self.user_id, uuid.uuid4(), quality=4, review_id=self.review_id)

    async def test_duplicate_key_from_concurrent_request_replays_committed_winner(self):
        card = self.make_card()
        response = self.make_response()
        winner = ReviewLog.model_construct(
            user_id=self.user_id,
            user_flashcard_id=self.card_id,
            review_id=self.review_id,
            quality=4,
            result=response.model_dump(mode="json"),
        )
        with (
            patch.object(
                ReviewLog,
                "find_one",
                new_callable=AsyncMock,
                side_effect=[None, winner],
            ) as find_log,
            patch.object(UserFlashcard, "get", new_callable=AsyncMock, return_value=card),
            patch.object(UserFlashcard, "save", new_callable=AsyncMock),
            patch.object(
                ReviewLog,
                "insert",
                new_callable=AsyncMock,
                side_effect=DuplicateKeyError("duplicate review id"),
            ),
        ):
            replayed = await submit_review(
                self.user_id, self.card_id, quality=4, review_id=self.review_id
            )

        self.assertEqual(replayed, response)
        self.assertEqual(find_log.await_count, 2)
        self.assertNotIn("session", find_log.await_args_list[1].kwargs)

    async def test_router_maps_review_id_conflict_to_http_409(self):
        user = User.model_construct(id=self.user_id)
        body = SubmitReviewRequest(review_id=self.review_id, quality=4)
        with patch(
            "app.routers.review.submit_review",
            new_callable=AsyncMock,
            side_effect=ReviewConflictError("REVIEW_ID_CONFLICT"),
        ):
            with self.assertRaises(HTTPException) as raised:
                await submit_card_review(self.card_id, body, user)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "REVIEW_ID_CONFLICT")


class TestTodayQueueLimit(unittest.IsolatedAsyncioTestCase):
    def make_user(self, goal: int) -> User:
        return User.model_construct(
            id=uuid.uuid4(),
            account_tier=AccountTier.FREE,
            preferences=UserPreferences(daily_review_goal=goal),
        )

    async def test_zero_effective_cap_returns_before_query(self):
        settings = SimpleNamespace(free_daily_review_cap=0)
        with (
            patch(
                "app.services.review_service.fetch_system_settings",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch.object(UserFlashcard, "find") as find_cards,
        ):
            result = await fetch_today_queue(self.make_user(goal=10))

        self.assertEqual(result, [])
        find_cards.assert_not_called()

    async def test_database_query_is_limited_to_effective_cap(self):
        global_id = uuid.uuid4()
        user_card = UserFlashcard.model_construct(
            id=uuid.uuid4(),
            global_flashcard_id=global_id,
            interval=1,
            repetitions=0,
            efactor=2.5,
            next_review_date=date(2026, 10, 11),
        )
        global_card = GlobalFlashcard.model_construct(
            id=global_id,
            keyword="chair",
            pronunciation=None,
            meaning_vi="cái ghế",
            example_1="A chair.",
            example_2="The chair is wooden.",
            related_words=[],
            audio_base64=None,
        )
        due_query = MagicMock()
        due_query.sort.return_value = due_query
        due_query.limit.return_value = due_query
        due_query.to_list = AsyncMock(return_value=[user_card])
        global_query = MagicMock()
        global_query.project.return_value = global_query
        global_query.to_list = AsyncMock(return_value=[global_card])
        settings = SimpleNamespace(free_daily_review_cap=2)

        with (
            patch(
                "app.services.review_service.fetch_system_settings",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch.object(UserFlashcard, "find", return_value=due_query),
            patch.object(GlobalFlashcard, "find", return_value=global_query),
        ):
            result = await fetch_today_queue(self.make_user(goal=10))

        due_query.sort.assert_called_once_with("next_review_date")
        due_query.limit.assert_called_once_with(2)
        due_query.to_list.assert_awaited_once_with()
        self.assertEqual([card.keyword for card in result], ["chair"])


if __name__ == "__main__":
    unittest.main()
