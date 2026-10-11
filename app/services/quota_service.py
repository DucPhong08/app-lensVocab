from __future__ import annotations

import logging
from datetime import UTC, datetime

import redis.asyncio as aioredis
from beanie.odm.utils.encoder import Encoder
from pymongo import ReturnDocument

from app.models.user import AccountTier, User
from app.services.system_setting_service import fetch_system_settings

logger = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    """Raise ở service layer, router tự convert sang HTTP 403."""

    pass


class MaintenanceModeError(Exception):
    """Raise khi hệ thống đang ở chế độ bảo trì."""

    pass


async def check_and_consume_quota(user: User, redis: aioredis.Redis) -> None:
    """
    Kiểm tra bảo trì và khấu trừ hạn mức scan theo gói cước (Free & Premium).
    Lấy quota động từ SystemSetting (Admin có thể tăng giảm mà không cần restart).
    """
    sys_settings = await fetch_system_settings()
    if sys_settings.maintenance_mode:
        raise MaintenanceModeError("MAINTENANCE_MODE")

    # Xác định hạn mức tối đa theo Tier
    if user.account_tier == AccountTier.PREMIUM:
        tier_daily_quota = sys_settings.premium_daily_quota
    else:
        tier_daily_quota = sys_settings.free_daily_quota

    # Keep the existing fail-closed policy when the scan cache is unavailable.
    await redis.ping()
    now = datetime.now(UTC)
    today = now.date()
    same_day = {"$eq": ["$quota_reset_date", today]}
    # Mongo is the single durable quota counter; stale User snapshots and Redis
    # eviction cannot reset a budget that another request has already consumed.
    remaining = {"$cond": [same_day, "$daily_quota_left", tier_daily_quota]}
    document = await User.get_motor_collection().find_one_and_update(
        Encoder().encode({"_id": user.id, "is_active": True, "$expr": {"$gt": [remaining, 0]}}),
        Encoder().encode(
            [
                {
                    "$set": {
                        "daily_quota_left": {"$subtract": [remaining, 1]},
                        "quota_reset_date": today,
                        "updated_at": now,
                    }
                }
            ]
        ),
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise QuotaExceededError("QUOTA_EXCEEDED")
    user.daily_quota_left = document["daily_quota_left"]
    user.quota_reset_date = today
    user.updated_at = now
