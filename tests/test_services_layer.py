from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.models.flashcard import FlashcardStatus, GlobalFlashcard, UserFlashcard
from app.models.review import ReviewLog
from app.models.user import AccountTier, User, UserPreferences
from app.schemas.flashcard import ConfirmFlashcardRequest
from app.schemas.user import UpdatePreferencesRequest
from app.services.flashcard_service import confirm_card
from app.services.review_service import FlashcardNotFoundError, submit_review
from app.services.user_service import read_preferences, update_preferences


class TestServicesLayer(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_id = uuid.uuid4()
        self.mock_user = User.model_construct(
            id=self.user_id,
            email="service_test@example.com",
            hashed_password="pw",
            display_name="Service Tester",
            account_tier=AccountTier.FREE,
            daily_quota_left=10,
            is_active=True,
            preferences=UserPreferences(),
        )

    # ── 1. User Service Tests ────────────────────────────────────────────────
    def test_get_preferences_returns_tier_policy(self):
        res = read_preferences(self.mock_user)
        self.assertEqual(res.account_tier, AccountTier.FREE)
        self.assertEqual(res.allowed_voices, ["Joanna"])
        self.assertFalse(res.allow_neural_voice)

    async def test_update_preferences_saves_to_user(self):
        with patch.object(User, "save", new_callable=AsyncMock) as mock_save:
            req = UpdatePreferencesRequest(daily_review_goal=18, max_detected_objects=4)
            res = await update_preferences(self.mock_user, req)
            mock_save.assert_called_once()
            self.assertEqual(res.preferences.daily_review_goal, 18)
            self.assertEqual(res.preferences.max_detected_objects, 4)

    # ── 2. Flashcard Service Tests ───────────────────────────────────────────
    @patch("app.services.flashcard_service.save_global_flashcard", new_callable=AsyncMock)
    @patch.object(UserFlashcard, "find_one", new_callable=AsyncMock)
    @patch.object(UserFlashcard, "insert", new_callable=AsyncMock)
    async def test_confirm_card_creates_user_card(
        self, mock_insert, mock_find_one, mock_save_global
    ):
        mock_find_one.return_value = None
        global_card = GlobalFlashcard.model_construct(
            id=uuid.uuid4(),
            keyword="keyboard",
            pronunciation="/ˈkiːbɔːrd/",
            meaning_vi="bàn phím",
            example_1="Type on keyboard.",
            example_2="Wireless keyboard.",
            related_words=["mouse"],
            audio_base64=None,
        )
        mock_save_global.return_value = global_card

        mock_redis = AsyncMock()
        req = ConfirmFlashcardRequest(
            keyword="keyboard",
            pronunciation="/ˈkiːbɔːrd/",
            meaning_vi="bàn phím",
            example_1="Type on keyboard.",
            example_2="Wireless keyboard.",
            related_words=["mouse"],
        )

        res = await confirm_card(self.user_id, req, mock_redis)
        self.assertEqual(res.keyword, "keyboard")
        self.assertEqual(res.meaning_vi, "bàn phím")
        self.assertEqual(res.status, FlashcardStatus.CONFIRMED)
        mock_insert.assert_called_once()

    # ── 3. Review Service Tests ──────────────────────────────────────────────
    @patch.object(UserFlashcard, "get", new_callable=AsyncMock)
    async def test_submit_review_not_found(self, mock_get):
        mock_get.return_value = None
        with self.assertRaises(FlashcardNotFoundError):
            await submit_review(self.user_id, uuid.uuid4(), quality=4)

    @patch.object(UserFlashcard, "get", new_callable=AsyncMock)
    @patch.object(UserFlashcard, "save", new_callable=AsyncMock)
    @patch.object(ReviewLog, "insert", new_callable=AsyncMock)
    async def test_submit_review_success(self, mock_log_insert, mock_card_save, mock_card_get):
        card = UserFlashcard.model_construct(
            id=uuid.uuid4(),
            user_id=self.user_id,
            global_flashcard_id=uuid.uuid4(),
            status=FlashcardStatus.CONFIRMED,
            interval=1,
            repetitions=0,
            efactor=2.5,
            total_reviews=0,
        )
        mock_card_get.return_value = card

        res = await submit_review(self.user_id, card.id, quality=4)
        mock_card_save.assert_called_once()
        mock_log_insert.assert_called_once()
        self.assertEqual(res.user_flashcard_id, card.id)
        self.assertEqual(res.repetitions_after, 1)
        self.assertIn("Ôn tập thành công", res.message)


if __name__ == "__main__":
    unittest.main()
