import unittest
import uuid
from datetime import timedelta
from app.services.auth_service import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestAuthService(unittest.TestCase):
    def test_hash_and_verify_password(self):
        password = "SecurePassword123!"
        hashed = hash_password(password)

        self.assertNotEqual(password, hashed)
        self.assertTrue(verify_password(password, hashed))
        self.assertFalse(verify_password("WrongPassword", hashed))

    def test_create_and_decode_access_token(self):
        user_id = uuid.uuid4()
        token = create_access_token(user_id)

        decoded_id_str = decode_access_token(token)
        self.assertEqual(decoded_id_str, str(user_id))

    def test_expired_token(self):
        user_id = uuid.uuid4()
        # Token đã hết hạn 1 phút trước
        token = create_access_token(user_id, expires_delta=timedelta(minutes=-1))

        decoded_id_str = decode_access_token(token)
        self.assertIsNone(decoded_id_str)

    def test_invalid_token(self):
        self.assertIsNone(decode_access_token("invalid.token.string"))


if __name__ == "__main__":
    unittest.main()
