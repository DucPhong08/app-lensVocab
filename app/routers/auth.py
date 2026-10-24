from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.auth import (
    GoogleLoginRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserMeResponse,
)
from app.services.auth_service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    UserInactiveError,
    UsernameAlreadyRegisteredError,
    authenticate_user,
    create_access_token,
    register_user,
)
from app.services.google_auth_service import GoogleAuthError, authenticate_google, link_google
from app.services.user_service import read_preferences

router = APIRouter()


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest) -> TokenResponse:
    """Đăng ký tài khoản mới và trả về JWT Bearer token."""
    try:
        user = await register_user(
            body.username, body.password, body.display_name, email=body.email
        )
    except UsernameAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail="USERNAME_ALREADY_REGISTERED") from exc
    except EmailAlreadyRegisteredError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="EMAIL_ALREADY_REGISTERED",
        )

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    """Đăng nhập bằng username hoặc email tài khoản cũ và mật khẩu và trả về JWT Bearer token."""
    try:
        user = await authenticate_user(body.username, body.password)
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
    response = UserMeResponse.model_validate(current_user, from_attributes=True)
    response.google_linked = bool(current_user.google_sub)
    response.preferences = read_preferences(current_user).preferences
    return response


@router.post("/auth/google", response_model=TokenResponse)
async def google_login(body: GoogleLoginRequest) -> TokenResponse:
    try:
        user = await authenticate_google(body.id_token)
    except GoogleAuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except UserInactiveError as exc:
        raise HTTPException(status_code=403, detail="USER_INACTIVE") from exc
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/auth/google/link")
async def google_link(
    body: GoogleLoginRequest, current_user: User = Depends(get_current_user)
) -> dict[str, str]:
    try:
        await link_google(current_user, body.id_token)
        return {"message": "GOOGLE_ACCOUNT_LINKED"}
    except GoogleAuthError as exc:
        # An invalid Google payload must not look like an expired app session.
        raise HTTPException(
            status_code=400 if exc.status == 401 else exc.status, detail=str(exc)
        ) from exc
