from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies.auth import get_current_user
from app.models.models import AccountTier, User, UserPreferences
from app.services.tier_service import (
    TierPolicyViolation,
    get_tier_policy,
    validate_user_preferences,
)

router = APIRouter()


class UpdatePreferencesRequest(BaseModel):
    preferred_voice_id: Optional[str] = Field(None, description="Tên giọng đọc AWS Polly")
    voice_speed: Optional[float] = Field(None, description="Tốc độ đọc (0.75, 1.0, 1.25)")
    daily_review_goal: Optional[int] = Field(
        None, ge=1, le=100, description="Mục tiêu số từ ôn tập mỗi ngày"
    )
    target_language: Optional[str] = Field(None, description="Mã ngôn ngữ mục tiêu (mặc định 'vi')")


class UserPreferencesResponse(BaseModel):
    preferences: UserPreferences
    account_tier: AccountTier
    allowed_voices: list[str]
    allowed_speeds: list[float]
    allow_neural_voice: bool
    max_daily_review_goal: int


@router.get("/users/me/preferences", response_model=UserPreferencesResponse)
async def get_my_preferences(
    current_user: User = Depends(get_current_user),
) -> UserPreferencesResponse:
    """Lấy thiết lập học tập cá nhân và danh sách quyền lợi theo gói cước."""
    policy = get_tier_policy(current_user.account_tier)
    return UserPreferencesResponse(
        preferences=current_user.preferences,
        account_tier=current_user.account_tier,
        allowed_voices=policy["allowed_voices"],
        allowed_speeds=policy["allowed_speeds"],
        allow_neural_voice=policy["allow_neural_voice"],
        max_daily_review_goal=policy["max_daily_review_goal"],
    )


@router.patch("/users/me/preferences", response_model=UserPreferencesResponse)
async def update_my_preferences(
    body: UpdatePreferencesRequest,
    current_user: User = Depends(get_current_user),
) -> UserPreferencesResponse:
    """
    Cập nhật thiết lập học tập cá nhân.
    Nếu người dùng chọn tính năng không thuộc gói cước (ví dụ Free chọn giọng Premium),
    hệ thống sẽ từ chối với HTTP 403 Forbidden.
    """
    current_pref = current_user.preferences.model_dump()
    update_data = body.model_dump(exclude_unset=True)

    updated_dict = {**current_pref, **update_data}
    new_pref = UserPreferences(**updated_dict)

    try:
        validate_user_preferences(current_user.account_tier, new_pref)
    except TierPolicyViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )

    current_user.preferences = new_pref
    await current_user.save()

    policy = get_tier_policy(current_user.account_tier)
    return UserPreferencesResponse(
        preferences=current_user.preferences,
        account_tier=current_user.account_tier,
        allowed_voices=policy["allowed_voices"],
        allowed_speeds=policy["allowed_speeds"],
        allow_neural_voice=policy["allow_neural_voice"],
        max_daily_review_goal=policy["max_daily_review_goal"],
    )
