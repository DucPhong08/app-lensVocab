from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from functools import partial

import requests
from google.auth import exceptions
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.models.user import User
from app.schemas.auth import _EMAIL_PATTERN
from app.services.auth_service import UserInactiveError


class GoogleAuthError(Exception):
    def __init__(self, code: str, status: int = 401):
        super().__init__(code)
        self.status = status


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str | None
    name: str | None


def _verify_google_token(token: str) -> GoogleIdentity:
    audience = settings.GOOGLE_WEB_CLIENT_ID.strip()
    if not audience:
        raise GoogleAuthError("GOOGLE_AUTH_NOT_CONFIGURED", 503)
    try:
        with requests.Session() as session:
            claims = id_token.verify_oauth2_token(
                token, partial(Request(session=session), timeout=5), audience=audience
            )
    except exceptions.TransportError as exc:
        raise GoogleAuthError("GOOGLE_AUTH_UNAVAILABLE", 503) from exc
    except (ValueError, exceptions.GoogleAuthError) as exc:
        raise GoogleAuthError("GOOGLE_TOKEN_INVALID") from exc
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise GoogleAuthError("GOOGLE_TOKEN_INVALID")
    email = claims.get("email") if claims.get("email_verified") is True else None
    if email is not None:
        if (
            not isinstance(email, str)
            or len(email) > 254
            or not re.fullmatch(_EMAIL_PATTERN, email)
        ):
            raise GoogleAuthError("GOOGLE_TOKEN_INVALID")
        email = email.strip().lower()
    name = claims.get("name")
    name = (name.strip()[:100] or None) if isinstance(name, str) else None
    return GoogleIdentity(subject, email, name)


async def verify_google_token(token: str) -> GoogleIdentity:
    # Verification includes signature, audience, issuer and expiry. Never trust client profile fields.
    return await asyncio.to_thread(_verify_google_token, token)


def _active(user: User) -> User:
    if not user.is_active:
        raise UserInactiveError("USER_INACTIVE")
    return user


async def authenticate_google(token: str) -> User:
    identity = await verify_google_token(token)
    existing = await User.find_one({"google_sub": identity.subject})
    if existing is not None:
        return _active(existing)
    if identity.email and await User.find_one({"email": identity.email}) is not None:
        # An optional, unverified local email must never become proof of account ownership.
        raise GoogleAuthError("GOOGLE_ACCOUNT_LINK_REQUIRED", 409)
    user = User(
        username=f"g_{uuid.uuid4().hex[:20]}",
        email=identity.email,
        google_sub=identity.subject,
        hashed_password=None,
        display_name=identity.name,
    )
    try:
        await user.insert()
    except DuplicateKeyError as exc:
        existing = await User.find_one({"google_sub": identity.subject})
        if existing is not None:
            return _active(existing)
        if identity.email and await User.find_one({"email": identity.email}) is not None:
            raise GoogleAuthError("GOOGLE_ACCOUNT_LINK_REQUIRED", 409) from exc
        raise GoogleAuthError("GOOGLE_ACCOUNT_CONFLICT", 409) from exc
    return user


async def link_google(current_user: User, token: str) -> None:
    identity = await verify_google_token(token)
    try:
        result = await User.find_one(
            {
                "_id": current_user.id,
                "$or": [{"google_sub": None}, {"google_sub": identity.subject}],
            }
        ).update({"$set": {"google_sub": identity.subject}})
    except DuplicateKeyError as exc:
        raise GoogleAuthError("GOOGLE_ACCOUNT_ALREADY_LINKED", 409) from exc
    if result.matched_count != 1:
        raise GoogleAuthError("GOOGLE_ACCOUNT_ALREADY_LINKED", 409)
