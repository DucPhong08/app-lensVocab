from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from pydantic import ValidationError
from pymongo.errors import AutoReconnect, DuplicateKeyError

from app.dependencies.auth import get_current_admin, get_current_user
from app.main import app
from app.models.user import User
from app.schemas.auth import RegisterRequest
from app.services import auth_service


class AuthTokenRegressionTests(unittest.TestCase):
    def test_zero_lifetime_is_not_replaced_by_the_default(self):
        now = datetime(2025, 1, 1, tzinfo=UTC)
        with patch.object(auth_service, "datetime") as clock:
            clock.now.return_value = now
            token = auth_service.create_access_token(uuid.uuid4(), timedelta(0))
        claims = jwt.get_unverified_claims(token)
        self.assertEqual(claims["exp"], claims["iat"])
        self.assertIsNone(auth_service.decode_access_token(token))

    def test_invalid_password_hash_is_rejected(self):
        self.assertFalse(auth_service.verify_password("password123", "invalid-hash"))

    def test_unexpected_bcrypt_failures_are_not_reported_as_wrong_passwords(self):
        with patch.object(
            auth_service.bcrypt, "checkpw", side_effect=RuntimeError("worker failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "worker failure"):
                auth_service.verify_password("password123", "hash")


class AuthValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_rejects_passwords_over_72_utf8_bytes(self):
        for password in ("x" * 73, "ắ" * 25):
            with self.subTest(byte_length=len(password.encode("utf-8"))):
                with self.assertRaises(ValidationError):
                    RegisterRequest(
                        username="learner", email="learner@example.com", password=password
                    )

    async def test_registration_accepts_exactly_72_bytes_without_modifying_password(self):
        for password in ("x" * 72, "ắ" * 24, " pass123 "):
            with self.subTest(byte_length=len(password.encode("utf-8"))):
                body = RegisterRequest(
                    username="learner", email="learner@example.com", password=password
                )
                self.assertEqual(body.password, password)

    async def test_long_password_is_rejected_before_registration_and_never_echoed(self):
        password = "secret-value-" * 7
        user = User.model_construct(email="learner@example.com", hashed_password="offline")
        with patch("app.routers.auth.register_user", AsyncMock(return_value=user)) as register:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/auth/register",
                    json={"username": "learner", "email": user.email, "password": password},
                )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(password, response.text)
        self.assertEqual(response.json()["detail"][0]["loc"], ["body", "password"])
        register.assert_not_awaited()

    async def test_short_password_validation_does_not_echo_the_password(self):
        password = "p@5x!"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/auth/register",
                json={"username": "learner", "email": "learner@example.com", "password": password},
            )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(password, response.text)

    async def test_successful_registration_keeps_the_existing_token_contract(self):
        user = User.model_construct(email="learner@example.com", hashed_password="offline")
        with patch("app.routers.auth.register_user", AsyncMock(return_value=user)):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/auth/register",
                    json={"username": "learner", "email": user.email, "password": "password123"},
                )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.json()), {"access_token", "token_type"})
        self.assertEqual(response.json()["token_type"], "bearer")
        self.assertEqual(
            auth_service.decode_access_token(response.json()["access_token"]), str(user.id)
        )


class AuthUserServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = User.model_construct(
            username="learner",
            email="learner@example.com",
            hashed_password="stored-hash",
            is_active=True,
        )
        self.model = MagicMock(return_value=self.user)
        self.model.find_one = AsyncMock(return_value=None)
        patcher = patch.object(auth_service, "User", self.model)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.insert = AsyncMock(return_value=self.user)
        patcher = patch.object(User, "insert", self.insert)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(auth_service, "hash_password", return_value="stored-hash")
        self.hash_password = patcher.start()
        self.addCleanup(patcher.stop)

    async def test_register_normalizes_identity_and_preserves_the_password(self):
        result = await auth_service.register_user(
            " Learner ", " pass123 ", "  Nguyễn Phong  ", email=" Learner@Example.COM "
        )
        self.assertIs(result, self.user)
        self.assertEqual(
            self.model.find_one.await_args_list,
            [
                unittest.mock.call({"username": "learner"}),
                unittest.mock.call({"email": "learner@example.com"}),
            ],
        )
        self.model.assert_called_once_with(
            username="learner",
            email="learner@example.com",
            hashed_password="stored-hash",
            display_name="Nguyễn Phong",
        )
        self.hash_password.assert_called_once_with(" pass123 ")
        self.insert.assert_awaited_once()

    async def test_existing_email_is_rejected_before_hashing_or_inserting(self):
        self.model.find_one.side_effect = [None, self.user]
        with self.assertRaises(auth_service.EmailAlreadyRegisteredError):
            await auth_service.register_user("learner", "password123", email=self.user.email)
        self.hash_password.assert_not_called()
        self.insert.assert_not_awaited()

    async def test_concurrent_duplicate_username_becomes_a_domain_error(self):
        self.insert.side_effect = [self.user, DuplicateKeyError("duplicate username")]
        results = await asyncio.gather(
            auth_service.register_user("learner", "password123"),
            auth_service.register_user("learner", "password123"),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, User) for result in results), 1)
        self.assertEqual(
            sum(
                isinstance(result, auth_service.UsernameAlreadyRegisteredError)
                for result in results
            ),
            1,
        )
        self.assertEqual(self.insert.await_count, 2)

    async def test_database_outage_is_not_misreported_as_duplicate_email(self):
        self.insert.side_effect = AutoReconnect("offline")
        with self.assertRaises(AutoReconnect):
            await auth_service.register_user("learner", "password123")

    async def test_unknown_user_does_not_attempt_password_verification(self):
        with patch.object(auth_service, "verify_password") as verify:
            with self.assertRaises(auth_service.InvalidCredentialsError):
                await auth_service.authenticate_user(self.user.email, "password123")
        verify.assert_not_called()

    async def test_wrong_password_is_rejected(self):
        self.model.find_one.return_value = self.user
        with patch.object(auth_service, "verify_password", return_value=False):
            with self.assertRaises(auth_service.InvalidCredentialsError):
                await auth_service.authenticate_user(self.user.email, "wrong-password")

    async def test_inactive_user_cannot_log_in_with_a_correct_password(self):
        self.user.is_active = False
        self.model.find_one.return_value = self.user
        with patch.object(auth_service, "verify_password", return_value=True):
            with self.assertRaises(auth_service.UserInactiveError):
                await auth_service.authenticate_user(self.user.email, "password123")

    async def test_valid_login_normalizes_email_and_preserves_password(self):
        self.model.find_one.return_value = self.user
        with patch.object(auth_service, "verify_password", return_value=True) as verify:
            result = await auth_service.authenticate_user(" Learner@Example.COM ", " pass123 ")
        self.assertIs(result, self.user)
        self.model.find_one.assert_awaited_once_with({"email": self.user.email})
        verify.assert_called_once_with(" pass123 ", self.user.hashed_password)


class AuthDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_credentials_keep_401_and_bearer_challenge_without_querying_mongo(self):
        cases = [
            (None, None, "NOT_AUTHENTICATED"),
            (
                HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"),
                None,
                "INVALID_OR_EXPIRED_TOKEN",
            ),
            (
                HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"),
                "not-a-uuid",
                "INVALID_TOKEN_SUBJECT",
            ),
        ]
        for credentials, subject, detail in cases:
            with self.subTest(detail=detail):
                with (
                    patch("app.dependencies.auth.decode_access_token", return_value=subject),
                    patch.object(User, "get", AsyncMock()) as find,
                ):
                    with self.assertRaises(HTTPException) as raised:
                        await get_current_user(credentials)
                self.assertEqual(raised.exception.status_code, 401)
                self.assertEqual(raised.exception.detail, detail)
                self.assertEqual(raised.exception.headers, {"WWW-Authenticate": "Bearer"})
                find.assert_not_awaited()

    async def test_missing_and_disabled_users_cannot_access_authenticated_routes(self):
        user_id = uuid.uuid4()
        inactive = User.model_construct(id=user_id, is_active=False)
        for user in (None, inactive):
            with self.subTest(missing=user is None):
                with (
                    patch("app.dependencies.auth.decode_access_token", return_value=str(user_id)),
                    patch.object(User, "get", AsyncMock(return_value=user)) as find,
                ):
                    with self.assertRaises(HTTPException) as raised:
                        await get_current_user(
                            HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
                        )
                self.assertEqual(raised.exception.detail, "USER_NOT_FOUND_OR_INACTIVE")
                self.assertEqual(raised.exception.status_code, 401)
                self.assertEqual(raised.exception.headers, {"WWW-Authenticate": "Bearer"})
                find.assert_awaited_once_with(user_id)

    async def test_active_user_is_returned_and_non_admin_still_gets_403(self):
        user = User.model_construct(is_active=True, is_admin=False)
        with (
            patch("app.dependencies.auth.decode_access_token", return_value=str(user.id)),
            patch.object(User, "get", AsyncMock(return_value=user)),
        ):
            result = await get_current_user(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
            )
        self.assertIs(result, user)
        with self.assertRaises(HTTPException) as raised:
            await get_current_admin(user)
        self.assertEqual(raised.exception.status_code, 403)
        user.is_admin = True
        self.assertIs(await get_current_admin(user), user)
