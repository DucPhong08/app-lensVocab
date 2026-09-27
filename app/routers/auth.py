from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies.auth import get_current_user
from app.models.models import AccountTier, User
from app.services.auth_service import create_access_token, hash_password, verify_password

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegisterRequest(BaseModel):
    email: str = Field(..., pattern=_EMAIL_PATTERN, description="Email người dùng")
    password: str = Field(..., min_length=6, description="Mật khẩu tối thiểu 6 ký tự")
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str = Field(..., pattern=_EMAIL_PATTERN, description="Email người dùng")
    password: str = Field(..., description="Mật khẩu")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserMeResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: Optional[str]
    account_tier: AccountTier
    daily_quota_left: int
    is_active: bool


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest) -> TokenResponse:
    """Đăng ký tài khoản mới và trả về JWT Bearer token."""
    normalized_email = body.email.strip().lower()

    existing_user = await User.find_one(User.email == normalized_email)
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="EMAIL_ALREADY_REGISTERED",
        )

    user = User(
        email=normalized_email,
        hashed_password=hash_password(body.password),
        display_name=body.display_name,
    )
    await user.insert()

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    """Đăng nhập bằng email/password và trả về JWT Bearer token."""
    normalized_email = body.email.strip().lower()

    user = await User.find_one(User.email == normalized_email)
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID_CREDENTIALS",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="USER_INACTIVE",
        )

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.get("/auth/me", response_model=UserMeResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserMeResponse:
    """Lấy thông tin người dùng hiện tại và hạn mức quét còn lại."""
    return UserMeResponse(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        account_tier=current_user.account_tier,
        daily_quota_left=current_user.daily_quota_left,
        is_active=current_user.is_active,
    )
