from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator

from app.models.user import AccountTier, UserPreferences

_EMAIL_PATTERN = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"


class RegisterRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "user@example.com",
                "password": "password123",
                "display_name": "Nguyen Van A",
            }
        }
    }

    email: str = Field(
        ...,
        pattern=_EMAIL_PATTERN,
        description="Email người dùng (vd: user@example.com)",
        examples=["user@example.com"],
    )
    password: str = Field(
        ...,
        min_length=6,
        description="Mật khẩu tối thiểu 6 ký tự, tối đa 72 byte UTF-8 (giới hạn bcrypt)",
        examples=["password123"],
    )
    display_name: str | None = Field(
        None,
        description="Tên hiển thị người dùng",
        examples=["Nguyen Van A"],
    )

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Mật khẩu không được vượt quá 72 byte UTF-8")
        return value


class LoginRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "user@example.com",
                "password": "password123",
            }
        }
    }

    email: str = Field(
        ...,
        pattern=_EMAIL_PATTERN,
        description="Email người dùng (vd: user@example.com)",
        examples=["user@example.com"],
    )
    password: str = Field(
        ...,
        description="Mật khẩu",
        examples=["password123"],
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserMeResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    account_tier: AccountTier
    daily_quota_left: int
    is_active: bool
    preferences: UserPreferences = Field(default_factory=UserPreferences)
