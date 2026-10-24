from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models.user import User
from app.services.auth_service import decode_access_token

security = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> User:
    """Dependency trích xuất Bearer token từ Authorization header và trả về User entity."""
    if credentials is None:
        raise _unauthorized("NOT_AUTHENTICATED")

    token = credentials.credentials
    user_id_str = decode_access_token(token)
    if not user_id_str:
        raise _unauthorized("INVALID_OR_EXPIRED_TOKEN")

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError as exc:
        raise _unauthorized("INVALID_TOKEN_SUBJECT") from exc

    user = await User.get(user_uuid)
    if user is None or not user.is_active:
        raise _unauthorized("USER_NOT_FOUND_OR_INACTIVE")

    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="ADMIN_REQUIRED")
    return user
