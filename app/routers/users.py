from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.user import UpdatePreferencesRequest, UserPreferencesResponse
from app.services.tier_service import TierPolicyViolation
from app.services.user_service import read_preferences, update_preferences

router = APIRouter()


@router.get("/users/me/preferences", response_model=UserPreferencesResponse)
async def get_my_preferences(
    current_user: User = Depends(get_current_user),
) -> UserPreferencesResponse:
    """Lấy thiết lập học tập cá nhân và danh sách quyền lợi theo gói cước."""
    return read_preferences(current_user)


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
    try:
        return await update_preferences(current_user, body)
    except TierPolicyViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
