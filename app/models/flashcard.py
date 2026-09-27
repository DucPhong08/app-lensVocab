from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from beanie import Document, Indexed
from pydantic import Field
from pymongo import ASCENDING, IndexModel

_DEFAULT_EFACTOR = 2.5


class FlashcardStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"


class GlobalFlashcard(Document):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    keyword: Annotated[str, Indexed(unique=True)]
    pronunciation: str | None = None  # Phiên âm IPA: /tʃer/
    meaning_vi: str  # Nghĩa tiếng Việt
    example_1: str  # Ví dụ câu 1
    example_2: str  # Ví dụ câu 2
    related_words: list[str] = []  # Từ liên quan: seat, sofa, stool
    audio_base64: str | None = None  # Giọng đọc từ vựng từ Polly (MP3 base64)
    embedding: list[float] | None = None  # Vector embedding 1024 chiều từ Titan v2

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "global_flashcards"
        indexes = ["keyword"]


class UserFlashcard(Document):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    global_flashcard_id: uuid.UUID

    status: FlashcardStatus = FlashcardStatus.DRAFT

    interval: int = 1
    repetitions: int = 0
    efactor: float = _DEFAULT_EFACTOR
    next_review_date: date = Field(default_factory=date.today)
    total_reviews: int = 0

    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_reviewed_at: datetime | None = None

    class Settings:
        name = "user_flashcards"
        indexes = [
            "user_id",
            "next_review_date",
            IndexModel([("user_id", ASCENDING), ("global_flashcard_id", ASCENDING)], unique=True),
        ]
