from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserMeResponse
from app.services.auth_service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    UserInactiveError,
    authenticate_user,
    create_access_token,
    register_user,
)

router = APIRouter()


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest) -> TokenResponse:
    """Đăng ký tài khoản mới và trả về JWT Bearer token."""
    try:
        user = await register_user(body.email, body.password, body.display_name)
    except EmailAlreadyRegisteredError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="EMAIL_ALREADY_REGISTERED",
        )

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    """Đăng nhập bằng email/password và trả về JWT Bearer token."""
    try:
        user = await authenticate_user(body.email, body.password)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID_CREDENTIALS",
        )
    except UserInactiveError:
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
        preferences=current_user.preferences,
    )
