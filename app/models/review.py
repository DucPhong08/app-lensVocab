from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from beanie import Document
from pydantic import Field


class ReviewQuality(int, enum.Enum):
    BLACKOUT = 0
    INCORRECT = 1
    INCORRECT_EASY = 2
    CORRECT_HARD = 3
    CORRECT = 4
    PERFECT = 5


class ReviewLog(Document):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    user_flashcard_id: uuid.UUID

    quality: int
    interval_before: int
    interval_after: int
    efactor_before: float
    efactor_after: float

    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "review_logs"
        indexes = ["user_id", "reviewed_at"]
