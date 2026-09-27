from __future__ import annotations

# Re-export all models for backwards compatibility
from app.models.flashcard import FlashcardStatus, GlobalFlashcard, UserFlashcard
from app.models.review import ReviewLog, ReviewQuality
from app.models.setting import SystemSetting
from app.models.user import AccountTier, User, UserPreferences

__all__ = [
    "AccountTier",
    "UserPreferences",
    "User",
    "FlashcardStatus",
    "GlobalFlashcard",
    "UserFlashcard",
    "ReviewQuality",
    "ReviewLog",
    "SystemSetting",
]
