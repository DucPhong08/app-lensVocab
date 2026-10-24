import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from jose import jwt

from app.config import settings
from app.models.setting import SystemSetting
from app.models.user import User
from app.services import vision_selection_service as selection
from app.services.ai_service import BoundingBox, RekognitionLabel, VisionResult
from app.services.auth_service import decode_access_token
from app.services.cache_service import FlashcardPayload


def detection(labels):
    return VisionResult(labels, labels[0].name, 99, labels[0].bounding_box, [], [], [], "objects")


class ObjectSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = User.model_construct()
        self.a = BoundingBox(0.2, 0.3, 0.1, 0.2)
        self.b = BoundingBox(0.2, 0.3, 0.6, 0.2)
        self.cup = RekognitionLabel("Cup", 98, [], [], [], self.a, True, [self.a, self.b])
        self.payload = FlashcardPayload(
            "cup", "/kʌp/", "cái cốc", "A cup.", "My cup.", [], None, "mongodb", False
        )

    async def test_multiple_instances_have_distinct_ids_and_diverse_labels_get_priority(self):
        book = RekognitionLabel("Book", 97, [], [], [], BoundingBox(0.2, 0.2, 0.5, 0.7), True)
        items = selection.detected_objects(detection([self.cup, book]), 3, self.user)
        self.assertEqual([item.keyword for item in items], ["Cup", "Book", "Cup"])
        self.assertEqual(len({item.object_id for item in items}), 3)
        self.assertEqual(items[2].bounding_box.left, 0.6)
        self.assertEqual(
            len(selection.detected_objects(detection([self.cup, book]), 2, self.user)), 2
        )

    async def test_same_region_alias_is_not_exposed_twice(self):
        alias = RekognitionLabel("Mug", 95, [], [], [], self.a, True)
        items = selection.detected_objects(detection([self.cup, alias]), 5, self.user)
        self.assertEqual(len(items), 2)
        self.assertEqual([x.keyword for x in items], ["Cup", "Cup"])

    async def test_selected_instance_keeps_its_box_and_generates_only_that_word(self):
        item = selection.detected_objects(detection([self.cup]), 5, self.user)[1]
        with (
            patch.object(
                selection,
                "fetch_system_settings",
                AsyncMock(return_value=SystemSetting.model_construct()),
            ),
            patch.object(
                selection, "get_flashcard", AsyncMock(return_value=self.payload)
            ) as lookup,
        ):
            result = await selection.select_object(item.selection_token, self.user, "fixture-redis")
        self.assertEqual(result.keyword, "cup")
        self.assertEqual(result.bounding_box.left, 0.6)
        self.assertEqual(result.selected_object_id, item.object_id)
        lookup.assert_awaited_once_with("Cup", "fixture-redis")
        self.assertIsNone(decode_access_token(item.selection_token))

    async def test_wrong_owner_anonymous_and_tampered_tokens_are_rejected_before_lookup(self):
        token = selection.detected_objects(detection([self.cup]), 5, self.user)[0].selection_token
        body = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        body["exp"] = int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())
        expired = jwt.encode(body, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        for candidate, user in (
            (token, None),
            (token, User.model_construct()),
            (token + "bad", self.user),
            (expired, self.user),
        ):
            with (
                self.subTest(candidate=candidate[:5]),
                patch.object(selection, "get_flashcard", AsyncMock()) as lookup,
                self.assertRaises(selection.ObjectSelectionError),
            ):
                await selection.select_object(candidate, user, None)
            lookup.assert_not_awaited()

    async def test_guest_selection_survives_guest_to_login_transition(self):
        item = selection.detected_objects(detection([self.cup]), 5, None)[0]
        with (
            patch.object(
                selection,
                "fetch_system_settings",
                AsyncMock(return_value=SystemSetting.model_construct()),
            ),
            patch.object(selection, "get_flashcard", AsyncMock(return_value=self.payload)),
        ):
            result = await selection.select_object(item.selection_token, self.user, None)
        self.assertEqual(result.keyword, "cup")

    async def test_maintenance_blocks_generation(self):
        token = selection.detected_objects(detection([self.cup]), 5, self.user)[0].selection_token
        with (
            patch.object(
                selection,
                "fetch_system_settings",
                AsyncMock(return_value=SystemSetting.model_construct(maintenance_mode=True)),
            ),
            patch.object(selection, "get_flashcard", AsyncMock()) as lookup,
            self.assertRaisesRegex(selection.ObjectSelectionError, "MAINTENANCE_MODE"),
        ):
            await selection.select_object(token, self.user, None)
        lookup.assert_not_awaited()

    async def test_invalid_and_weak_boxes_never_get_selection_tokens(self):
        invalid = RekognitionLabel("Bad", 99, [], [], [], BoundingBox(0.5, 0.5, 0.9, 0), True)
        weak = RekognitionLabel("Weak", 30, [], [], [], self.a, True)
        items = selection.detected_objects(detection([invalid, weak]), 5, self.user)
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].selection_token)

    async def test_aliases_are_deduplicated_before_limiting_the_number_of_objects(self):
        alias = RekognitionLabel("Mug", 97, [], [], [], self.a, True)
        book = RekognitionLabel("Book", 96, [], [], [], BoundingBox(0.2, 0.2, 0.5, 0.7), True)
        result = detection([self.cup])
        result.candidates = [self.cup, alias, book]
        items = selection.detected_objects(result, 3, self.user)
        self.assertEqual([item.keyword for item in items], ["Cup", "Book", "Cup"])
