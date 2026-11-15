from unittest.mock import patch

from app.models.user import User


def mock_preference_writes():
    async def apply(user, changes):
        for path, value in changes.items():
            prefix, field = path.split(".")
            assert prefix == "preferences" and field in type(user.preferences).model_fields
            setattr(user.preferences, field, value)
        return user

    return patch.object(User, "set", autospec=True, side_effect=apply)
