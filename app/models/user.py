from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from beanie import Document, Indexed
from pydantic import BaseModel, Field

_DEFAULT_FREE_QUOTA = 10


class AccountTier(str, enum.Enum):
    FREE = "FREE"
    PREMIUM = "PREMIUM"


class UserPreferences(BaseModel):
    preferred_voice_id: str = "Joanna"  # Joanna, Matthew, Amy, Brian, Olivia
    voice_speed: float = 1.0  # 0.75, 1.0, 1.25
    daily_review_goal: int = 15  # Mục tiêu số từ ôn mỗi ngày (Anti-demotivation)
    target_language: str = "vi"  # Ngôn ngữ giải nghĩa
    max_detected_objects: int = Field(
        default=5, ge=1, le=10, description="Số vật thể tối đa muốn phát hiện trong 1 ảnh"
    )


class User(Document):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    email: Annotated[str, Indexed(unique=True)]
    hashed_password: str
    display_name: str | None = None
    account_tier: AccountTier = AccountTier.FREE
    preferences: UserPreferences = Field(default_factory=UserPreferences)

    daily_quota_left: int = _DEFAULT_FREE_QUOTA
    quota_reset_date: date | None = None

    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "users"
        indexes = ["email"]
