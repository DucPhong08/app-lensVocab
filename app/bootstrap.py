from __future__ import annotations

from app.config import settings
from app.database import get_motor_client
from app.models.flashcard import GlobalFlashcard, UserFlashcard
from app.models.review import ReviewLog
from app.models.setting import SystemSetting
from app.models.user import User


async def setup_beanie() -> None:
    """Khởi tạo Beanie — gọi một lần duy nhất trong FastAPI lifespan."""
    from beanie import init_beanie

    client = get_motor_client()
    await init_beanie(
        database=client[settings.MONGODB_DB_NAME],
        document_models=[User, GlobalFlashcard, UserFlashcard, ReviewLog, SystemSetting],
    )
