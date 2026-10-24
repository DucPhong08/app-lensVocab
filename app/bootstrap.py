from __future__ import annotations

from pymongo.errors import OperationFailure

from app.config import settings
from app.database import get_motor_client
from app.models.flashcard import GlobalFlashcard, UserFlashcard
from app.models.review import ReviewLog
from app.models.setting import SystemSetting
from app.models.user import User


async def ensure_user_indexes(database) -> None:
    """Create partial unique indexes before replacing the legacy email-only index."""
    collection = database["users"]
    for index in User.Settings.indexes:
        await collection.create_indexes([index])
    indexes = await collection.list_indexes().to_list(length=None)
    for index in indexes:
        if list(index["key"].items()) != [("email", 1)] or index["name"] != "email_1":
            continue
        try:
            await collection.drop_index("email_1")
        except OperationFailure as exc:
            # Another starting worker may already have removed the same legacy index.
            if exc.code != 27:
                raise


async def setup_beanie() -> None:
    """Khởi tạo Beanie — gọi một lần duy nhất trong FastAPI lifespan."""
    from beanie import init_beanie

    client = get_motor_client()
    await ensure_user_indexes(client[settings.MONGODB_DB_NAME])
    await init_beanie(
        database=client[settings.MONGODB_DB_NAME],
        document_models=[User, GlobalFlashcard, UserFlashcard, ReviewLog, SystemSetting],
    )
