from __future__ import annotations

import unittest
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from beanie.odm.utils.projection import get_projection

from app.dependencies.auth import get_current_user
from app.main import app
from app.models.flashcard import FlashcardStatus, GlobalFlashcard, UserFlashcard
from app.models.user import AccountTier, User, UserPreferences
from app.schemas.flashcard import FlashcardAudioResponse, FlashcardPageResponse, FlashcardText
from app.services.flashcard_service import CardAudioNotFoundError, get_card_audio, list_card_page
from app.services.tier_service import TierPolicyViolation, validate_user_preferences
from app.services.user_service import read_preferences


class FlashcardDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_is_bounded_sorted_and_does_not_load_audio_or_embedding(self):
        owner, global_id = uuid.uuid4(), uuid.uuid4()
        card = UserFlashcard.model_construct(
            user_id=owner,
            global_flashcard_id=global_id,
            status=FlashcardStatus.CONFIRMED,
            next_review_date=date(2026, 10, 24),
        )
        content = FlashcardText(
            _id=global_id,
            keyword="chair",
            meaning_vi="ghế",
            example_1="A chair.",
            example_2="My chair.",
        )
        query = MagicMock()
        for method in ("sort", "skip", "limit"):
            getattr(query, method).return_value = query
        query.count = AsyncMock(return_value=4)
        query.to_list = AsyncMock(return_value=[card])
        contents = MagicMock()
        contents.project.return_value = contents
        contents.to_list = AsyncMock(return_value=[content])
        with (
            patch.object(UserFlashcard, "find", return_value=query) as find,
            patch.object(GlobalFlashcard, "find", return_value=contents),
        ):
            page = await list_card_page(owner, limit=1, offset=2)
        self.assertEqual(page.total, 4)
        self.assertEqual(page.next_offset, 3)
        self.assertEqual(page.items[0].keyword, "chair")
        self.assertIsNone(page.items[0].audio_base64)
        find.assert_called_with({"user_id": owner})
        query.sort.assert_called_once_with([("added_at", -1), ("_id", 1)])
        query.skip.assert_called_once_with(2)
        query.limit.assert_called_once_with(1)
        projection = get_projection(FlashcardText)
        self.assertNotIn("audio_base64", projection)
        self.assertNotIn("embedding", projection)

    async def test_audio_ownership_is_checked_before_global_content(self):
        with (
            patch.object(UserFlashcard, "find_one", AsyncMock(return_value=None)),
            patch.object(GlobalFlashcard, "find_one") as global_find,
        ):
            with self.assertRaisesRegex(CardAudioNotFoundError, "FLASHCARD_NOT_FOUND"):
                await get_card_audio(uuid.uuid4(), uuid.uuid4())
        global_find.assert_not_called()

    async def test_owned_audio_uses_only_the_audio_projection(self):
        owner, card_id, global_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        card = UserFlashcard.model_construct(
            id=card_id, user_id=owner, global_flashcard_id=global_id
        )
        query = MagicMock()
        query.project = AsyncMock(return_value=FlashcardAudioResponse(audio_base64="offline-mp3"))
        with (
            patch.object(UserFlashcard, "find_one", AsyncMock(return_value=card)) as owned,
            patch.object(GlobalFlashcard, "find_one", return_value=query) as content,
        ):
            result = await get_card_audio(owner, card_id)
        owned.assert_awaited_once_with({"_id": card_id, "user_id": owner})
        content.assert_called_once_with({"_id": global_id})
        query.project.assert_awaited_once_with(FlashcardAudioResponse)
        self.assertEqual(result.audio_base64, "offline-mp3")

    async def test_missing_audio_returns_a_domain_error_without_calling_aws(self):
        query = MagicMock()
        query.project = AsyncMock(return_value=FlashcardAudioResponse())
        with (
            patch.object(
                UserFlashcard,
                "find_one",
                AsyncMock(
                    return_value=UserFlashcard.model_construct(global_flashcard_id=uuid.uuid4())
                ),
            ),
            patch.object(GlobalFlashcard, "find_one", return_value=query),
        ):
            with self.assertRaisesRegex(CardAudioNotFoundError, "AUDIO_NOT_AVAILABLE"):
                await get_card_audio(uuid.uuid4(), uuid.uuid4())

    async def test_delivery_routes_enforce_auth_and_pagination_bounds(self):
        user = User.model_construct()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in ("/flashcards/page", f"/flashcards/{uuid.uuid4()}/audio"):
                self.assertEqual((await client.get(path)).status_code, 401)
            app.dependency_overrides[get_current_user] = lambda: user
            try:
                with patch(
                    "app.routers.flashcards.list_card_page",
                    AsyncMock(
                        return_value=FlashcardPageResponse(items=[], total=0, next_offset=None)
                    ),
                ) as page:
                    for query in ("limit=0", "limit=101", "offset=-1"):
                        self.assertEqual(
                            (await client.get("/flashcards/page?" + query)).status_code, 422
                        )
                    page.assert_not_awaited()
                    response = await client.get("/flashcards/page?limit=20&offset=40")
                    self.assertEqual(
                        response.json(), {"items": [], "total": 0, "next_offset": None}
                    )
                    page.assert_awaited_once_with(user.id, limit=20, offset=40)
            finally:
                app.dependency_overrides.clear()

    async def test_standard_voice_is_truthful_and_speeds_work_for_both_tiers(self):
        for tier in AccountTier:
            for speed in (0.75, 1.0, 1.25):
                validate_user_preferences(tier, UserPreferences(voice_speed=speed))
            with self.assertRaises(TierPolicyViolation):
                validate_user_preferences(tier, UserPreferences(preferred_voice_id="Amy"))
        user = User.model_construct(preferences=UserPreferences(preferred_voice_id="Amy"))
        response = read_preferences(user)
        self.assertEqual(response.allowed_voices, ["Joanna"])
        self.assertEqual(response.preferences.preferred_voice_id, "Joanna")
        self.assertEqual(user.preferences.preferred_voice_id, "Amy")
