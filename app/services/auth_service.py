from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.models.user import User


def hash_password(password: str) -> str:
    """Hash mật khẩu bằng native bcrypt, trả về chuỗi utf-8."""
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Kiểm tra mật khẩu plain-text với bản hash."""
    plain_bytes = plain_password.encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")
    try:
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except ValueError:
        return False


def create_access_token(user_id: uuid.UUID, expires_delta: timedelta | None = None) -> str:
    """Tạo JWT access token chứa subject là user_id (string UUID)."""
    now = datetime.now(UTC)
    lifetime = (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    expire = now + lifetime

    to_encode = {
        "sub": str(user_id),
        "iat": now,
        "exp": expire,
    }
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> str | None:
    """Giải mã token, trả về subject (user_id dạng str) hoặc None nếu không hợp lệ."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id_str: str | None = payload.get("sub")
        return user_id_str
    except JWTError:
        return None


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class UserInactiveError(Exception):
    pass


async def register_user(email: str, password: str, display_name: str | None = None) -> User:
    """Đăng ký tài khoản, dựa vào unique index để chặn email trùng khi có cạnh tranh."""
    normalized_email = email.strip().lower()
    existing_user = await User.find_one({"email": normalized_email})
    if existing_user is not None:
        raise EmailAlreadyRegisteredError("EMAIL_ALREADY_REGISTERED")

    user = User(
        email=normalized_email,
        hashed_password=await asyncio.to_thread(hash_password, password),
        display_name=(display_name.strip() or None) if display_name is not None else None,
    )
    try:
        await user.insert()
    except DuplicateKeyError as exc:
        raise EmailAlreadyRegisteredError("EMAIL_ALREADY_REGISTERED") from exc
    return user


async def authenticate_user(email: str, password: str) -> User:
    """Xác thực người dùng bằng email và mật khẩu."""
    normalized_email = email.strip().lower()
    user = await User.find_one({"email": normalized_email})
    if user is None or not await asyncio.to_thread(verify_password, password, user.hashed_password):
        raise InvalidCredentialsError("INVALID_CREDENTIALS")

    if not user.is_active:
        raise UserInactiveError("USER_INACTIVE")

    return user
