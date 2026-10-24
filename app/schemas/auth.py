from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import AliasChoices, BaseModel, Field, StringConstraints, field_validator

from app.models.user import AccountTier, UserPreferences

_EMAIL_PATTERN = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
Username = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=30,
        pattern=r"^[a-z0-9][a-z0-9_.]{2,29}$",
    ),
]


class RegisterRequest(BaseModel):
    username: Username
    password: str = Field(min_length=6, max_length=72, repr=False)
    email: str | None = Field(None, max_length=254, pattern=_EMAIL_PATTERN)
    display_name: str | None = Field(None, max_length=100)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return (value.strip().lower() or None) if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Mật khẩu không được vượt quá 72 byte UTF-8")
        return value


class LoginRequest(BaseModel):
    username: str = Field(
        validation_alias=AliasChoices("username", "email"), min_length=1, max_length=254
    )
    password: str = Field(max_length=256, repr=False)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_identity(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=1, max_length=8192, repr=False)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserMeResponse(BaseModel):
    id: uuid.UUID
    username: str | None = None
    email: str | None = None
    display_name: str | None
    google_linked: bool = False
    account_tier: AccountTier
    daily_quota_left: int
    is_active: bool
    preferences: UserPreferences = Field(default_factory=UserPreferences)
