from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.user import AccountTier, UserPreferences


class UpdatePreferencesRequest(BaseModel):
    preferred_voice_id: str | None = Field(None, description="Tên giọng đọc AWS Polly")
    voice_speed: float | None = Field(None, description="Tốc độ đọc (0.75, 1.0, 1.25)")
    daily_review_goal: int | None = Field(
        None, ge=1, le=100, description="Mục tiêu số từ ôn tập mỗi ngày"
    )
    target_language: str | None = Field(None, description="Mã ngôn ngữ mục tiêu (mặc định 'vi')")
    max_detected_objects: int | None = Field(
        None, ge=1, le=10, description="Số vật thể tối đa muốn phát hiện trong 1 ảnh (1-10)"
    )


class UserPreferencesResponse(BaseModel):
    preferences: UserPreferences
    account_tier: AccountTier
    allowed_voices: list[str]
    allowed_speeds: list[float]
    allow_neural_voice: bool
    max_daily_review_goal: int
