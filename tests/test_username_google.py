from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from app.main import app
from app.models.user import User
from app.schemas.auth import RegisterRequest


class UsernameContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_without_email_issues_session(self):
        user = User.model_construct(username="phong", email=None, hashed_password="offline")
        with patch("app.routers.auth.register_user", AsyncMock(return_value=user)) as register:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/auth/register", json={"username": "Phong", "password": "secret123"}
                )
        self.assertEqual(response.status_code, 201)
        register.assert_awaited_once_with("phong", "secret123", None, email=None)

    async def test_username_login_and_legacy_email_login(self):
        user = User.model_construct(username="phong")
        with patch("app.routers.auth.authenticate_user", AsyncMock(return_value=user)) as login:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                for data, identity in (
                    ({"username": "Phong"}, "phong"),
                    ({"email": "Old@Example.com"}, "old@example.com"),
                ):
                    response = await client.post(
                        "/auth/login", json={**data, "password": "secret123"}
                    )
                    self.assertEqual(response.status_code, 200)
                    login.assert_awaited_with(identity, "secret123")

    async def test_username_is_required_and_optional_email_is_validated(self):
        body = RegisterRequest(username=" Phong_1 ", password="secret123", email="")
        self.assertEqual(body.username, "phong_1")
        self.assertIsNone(body.email)
        for username in ("ab", "with space", "a@example.com", "$username", "a" * 31):
            with self.subTest(username=username), self.assertRaises(ValidationError):
                RegisterRequest(username=username, password="secret123")
        with self.assertRaises(ValidationError):
            RegisterRequest(username="phong", email="invalid", password="secret123")

    async def test_google_is_disabled_without_server_configuration(self):
        with patch("app.config.settings.GOOGLE_WEB_CLIENT_ID", ""):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/auth/google", json={"id_token": "untrusted-token"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "GOOGLE_AUTH_NOT_CONFIGURED")


class GoogleVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private = cls.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        cls.public = (
            cls.key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )

    def token(self, **changes):
        import time

        from jose import jwt

        claims = {
            "iss": "https://accounts.google.com",
            "aud": "test-web-client",
            "sub": "google-subject",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "email": "learner@example.com",
            "email_verified": True,
            "name": "Learner",
        }
        claims.update(changes)
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": "test-key"})

    def verify(self, token):
        import json
        from types import SimpleNamespace

        from app.services import google_auth_service as google

        with (
            patch.object(google.settings, "GOOGLE_WEB_CLIENT_ID", "test-web-client"),
            patch.object(google, "Request") as transport,
        ):
            transport.return_value.return_value = SimpleNamespace(
                status=200, data=json.dumps({"test-key": self.public}).encode()
            )
            result = google._verify_google_token(token)
            self.assertEqual(transport.return_value.call_args.kwargs["timeout"], 5)
            return result

    def test_signed_token_is_verified_by_google_library(self):
        identity = self.verify(self.token())
        self.assertEqual(identity.subject, "google-subject")
        self.assertEqual(identity.email, "learner@example.com")

    def test_wrong_audience_expiry_issuer_or_subject_is_rejected(self):
        from app.services.google_auth_service import GoogleAuthError

        for claims in (
            {"aud": "other-app"},
            {"exp": 1},
            {"iss": "https://attacker.invalid"},
            {"sub": ""},
        ):
            with (
                self.subTest(claims=claims),
                self.assertRaisesRegex(GoogleAuthError, "GOOGLE_TOKEN_INVALID"),
            ):
                self.verify(self.token(**claims))

    def test_forged_signature_is_rejected(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from jose import jwt

        from app.services.google_auth_service import GoogleAuthError

        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key = other.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        forged = jwt.encode(
            jwt.get_unverified_claims(self.token()),
            key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )
        with self.assertRaisesRegex(GoogleAuthError, "GOOGLE_TOKEN_INVALID"):
            self.verify(forged)

    def test_unverified_google_email_is_not_saved_as_contact_email(self):
        self.assertIsNone(self.verify(self.token(email_verified=False)).email)

    def test_settings_repr_does_not_disclose_secret_fields(self):
        from app.config import Settings

        config = Settings(
            _env_file=None,
            SECRET_KEY="fixture-secret",
            MONGODB_URL="mongodb://fixture-db-secret",
            REDIS_URL="redis://fixture-redis-secret",
            AWS_ACCESS_KEY_ID="fixture-access",
            AWS_SECRET_ACCESS_KEY="fixture-aws-secret",
        )
        for value in (
            "fixture-secret",
            "fixture-db-secret",
            "fixture-redis-secret",
            "fixture-access",
            "fixture-aws-secret",
        ):
            self.assertNotIn(value, repr(config))


class GoogleAccountTests(unittest.IsolatedAsyncioTestCase):
    async def test_email_collision_never_automatically_links_accounts(self):
        from unittest.mock import MagicMock

        from app.services import google_auth_service as google

        model = MagicMock()
        model.find_one = AsyncMock(side_effect=[None, User.model_construct()])
        with (
            patch.object(google, "User", model),
            patch.object(
                google,
                "verify_google_token",
                AsyncMock(
                    return_value=google.GoogleIdentity("sub", "existing@example.com", "Name")
                ),
            ),
        ):
            with self.assertRaisesRegex(google.GoogleAuthError, "GOOGLE_ACCOUNT_LINK_REQUIRED"):
                await google.authenticate_google("token")
        model.assert_not_called()

    async def test_new_google_account_has_username_without_local_password(self):
        from unittest.mock import MagicMock

        from app.services import google_auth_service as google

        user = User.model_construct()
        model = MagicMock(return_value=user)
        model.find_one = AsyncMock(return_value=None)
        with (
            patch.object(google, "User", model),
            patch.object(User, "insert", AsyncMock()),
            patch.object(
                google,
                "verify_google_token",
                AsyncMock(return_value=google.GoogleIdentity("sub", None, "Name")),
            ),
        ):
            self.assertIs(await google.authenticate_google("token"), user)
        data = model.call_args.kwargs
        self.assertTrue(data["username"].startswith("g_"))
        self.assertIsNone(data["hashed_password"])
        self.assertIsNone(data["email"])
        self.assertEqual(data["google_sub"], "sub")

    async def test_google_only_account_cannot_use_password_login(self):
        from app.services import auth_service

        user = User.model_construct(username="google_user", hashed_password=None)
        with (
            patch.object(User, "find_one", AsyncMock(return_value=user)),
            patch.object(auth_service, "verify_password") as verify,
        ):
            with self.assertRaises(auth_service.InvalidCredentialsError):
                await auth_service.authenticate_user("google_user", "anything")
        verify.assert_not_called()

    async def test_google_subject_identifies_existing_account_even_if_email_changed(self):
        from app.services import google_auth_service as google

        user = User.model_construct(google_sub="sub", email="old@example.com")
        with (
            patch.object(User, "find_one", AsyncMock(return_value=user)) as find,
            patch.object(
                google,
                "verify_google_token",
                AsyncMock(return_value=google.GoogleIdentity("sub", "new@example.com", None)),
            ),
        ):
            self.assertIs(await google.authenticate_google("token"), user)
        find.assert_awaited_once_with({"google_sub": "sub"})

    async def test_disabled_google_account_is_rejected(self):
        from app.services import google_auth_service as google
        from app.services.auth_service import UserInactiveError

        with (
            patch.object(
                User, "find_one", AsyncMock(return_value=User.model_construct(is_active=False))
            ),
            patch.object(
                google,
                "verify_google_token",
                AsyncMock(return_value=google.GoogleIdentity("sub", None, None)),
            ),
        ):
            with self.assertRaises(UserInactiveError):
                await google.authenticate_google("token")

    async def test_concurrent_google_registration_returns_the_winning_account(self):
        from unittest.mock import MagicMock

        from pymongo.errors import DuplicateKeyError

        from app.services import google_auth_service as google

        winner = User.model_construct(google_sub="sub")
        model = MagicMock(return_value=User.model_construct())
        model.find_one = AsyncMock(side_effect=[None, winner])
        with (
            patch.object(google, "User", model),
            patch.object(User, "insert", AsyncMock(side_effect=DuplicateKeyError("fixture"))),
            patch.object(
                google,
                "verify_google_token",
                AsyncMock(return_value=google.GoogleIdentity("sub", None, None)),
            ),
        ):
            self.assertIs(await google.authenticate_google("token"), winner)

    async def test_link_is_atomic_and_rejects_an_already_linked_google_identity(self):
        from unittest.mock import MagicMock

        from pymongo.errors import DuplicateKeyError

        from app.services import google_auth_service as google

        user = User.model_construct()
        query = MagicMock()
        query.update = AsyncMock(side_effect=DuplicateKeyError("fixture"))
        with (
            patch.object(User, "find_one", return_value=query) as find,
            patch.object(
                google,
                "verify_google_token",
                AsyncMock(return_value=google.GoogleIdentity("sub", None, None)),
            ),
        ):
            with self.assertRaisesRegex(google.GoogleAuthError, "GOOGLE_ACCOUNT_ALREADY_LINKED"):
                await google.link_google(user, "token")
        self.assertEqual(
            find.call_args.args[0],
            {"_id": user.id, "$or": [{"google_sub": None}, {"google_sub": "sub"}]},
        )

    async def test_link_requires_login_and_me_exposes_no_google_subject_or_password(self):
        from app.dependencies.auth import get_current_user

        user = User.model_construct(
            username="learner", email=None, google_sub="private-sub", hashed_password="private-hash"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            self.assertEqual(
                (await client.post("/auth/google/link", json={"id_token": "fixture"})).status_code,
                401,
            )
            app.dependency_overrides[get_current_user] = lambda: user
            try:
                response = await client.get("/auth/me")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["google_linked"])
                self.assertIsNone(response.json()["email"])
                self.assertNotIn("private-sub", response.text)
                self.assertNotIn("private-hash", response.text)
            finally:
                app.dependency_overrides.clear()


class AuthIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_constraints_exist_before_legacy_email_index_is_removed(self):
        from unittest.mock import MagicMock

        from app.bootstrap import ensure_user_indexes

        events = []
        collection = MagicMock()
        collection.create_indexes = AsyncMock(
            side_effect=lambda indexes: events.append(("create", indexes[0].document))
        )
        collection.list_indexes.return_value.to_list = AsyncMock(
            return_value=[
                {"name": "_id_", "key": {"_id": 1}},
                {"name": "email_1", "key": {"email": 1}, "unique": True},
                {"name": "email_unique_present", "key": {"email": 1}, "unique": True},
                {"name": "other_index", "key": {"created_at": 1}},
            ]
        )
        collection.drop_index = AsyncMock(side_effect=lambda name: events.append(("drop", name)))
        await ensure_user_indexes({"users": collection})
        self.assertEqual(len(events), 4)
        self.assertEqual(events[-1], ("drop", "email_1"))
        for _, index in events[:3]:
            field = next(iter(index["key"]))
            self.assertTrue(index["unique"])
            self.assertEqual(index["partialFilterExpression"], {field: {"$type": "string"}})


class GoogleLinkRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_google_token_does_not_expire_an_existing_app_session(self):
        from app.dependencies.auth import get_current_user
        from app.services.google_auth_service import GoogleAuthError

        app.dependency_overrides[get_current_user] = lambda: User.model_construct()
        try:
            with patch(
                "app.routers.auth.link_google",
                AsyncMock(side_effect=GoogleAuthError("GOOGLE_TOKEN_INVALID")),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    result = await client.post("/auth/google/link", json={"id_token": "invalid"})
            self.assertEqual(result.status_code, 400)
            self.assertEqual(result.json()["detail"], "GOOGLE_TOKEN_INVALID")
        finally:
            app.dependency_overrides.clear()

    async def test_successful_google_login_issues_only_an_app_jwt(self):
        from app.services.auth_service import decode_access_token

        user = User.model_construct()
        with patch("app.routers.auth.authenticate_google", AsyncMock(return_value=user)):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/auth/google", json={"id_token": "google-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(decode_access_token(response.json()["access_token"]), str(user.id))
        self.assertNotIn("google-token", response.text)
