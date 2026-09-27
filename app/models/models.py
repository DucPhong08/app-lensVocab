from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Optional

from beanie import Document, Indexed
from pydantic import Field


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class AccountTier(str, enum.Enum):
    FREE = "FREE"
    PREMIUM = "PREMIUM"


class FlashcardStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"


class ReviewQuality(int, enum.Enum):
    BLACKOUT = 0
    INCORRECT = 1
    INCORRECT_EASY = 2
    CORRECT_HARD = 3
    CORRECT = 4
    PERFECT = 5


# ─────────────────────────────────────────────────────────────────────────────
# Constants — không import config vào model layer
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_FREE_QUOTA = 10
_DEFAULT_EFACTOR = 2.5
_EMBEDDING_DIM = 1024


# ─────────────────────────────────────────────────────────────────────────────
# User
# ─────────────────────────────────────────────────────────────────────────────

class User(Document):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    email: Annotated[str, Indexed(unique=True)]
    hashed_password: str
    display_name: Optional[str] = None
    account_tier: AccountTier = AccountTier.FREE

    daily_quota_left: int = _DEFAULT_FREE_QUOTA
    quota_reset_date: Optional[date] = None

    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "users"
        indexes = ["email"]


# ─────────────────────────────────────────────────────────────────────────────
# GlobalFlashcard
# ─────────────────────────────────────────────────────────────────────────────

class GlobalFlashcard(Document):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    keyword: Annotated[str, Indexed(unique=True)]
    pronunciation: Optional[str] = None       # Phiên âm IPA: /tʃer/
    meaning_vi: str                           # Nghĩa tiếng Việt
    example_1: str                            # Ví dụ câu 1
    example_2: str                            # Ví dụ câu 2
    related_words: list[str] = []             # Từ liên quan: seat, sofa, stool
    audio_base64: Optional[str] = None        # Giọng đọc từ vựng từ Polly (MP3 base64)
    embedding: Optional[list[float]] = None   # Vector embedding 1024 chiều từ Titan v2

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "global_flashcards"
        indexes = ["keyword"]


# ─────────────────────────────────────────────────────────────────────────────
# UserFlashcard
# ─────────────────────────────────────────────────────────────────────────────

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

    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_reviewed_at: Optional[datetime] = None

    class Settings:
        name = "user_flashcards"
        indexes = [
            "user_id",
            "next_review_date",
            [("user_id", 1), ("global_flashcard_id", 1)],
        ]


# ─────────────────────────────────────────────────────────────────────────────
# ReviewLog
# ─────────────────────────────────────────────────────────────────────────────

class ReviewLog(Document):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    user_flashcard_id: uuid.UUID

    quality: int
    interval_before: int
    interval_after: int
    efactor_before: float
    efactor_after: float

    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "review_logs"
        indexes = ["user_id", "reviewed_at"]
