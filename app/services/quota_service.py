from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import redis.asyncio as aioredis

from app.config import settings
from app.models.models import AccountTier, User

logger = logging.getLogger(__name__)


def _quota_redis_key(user_id: str) -> str:
    return f"quota:{user_id}"


class QuotaExceededError(Exception):
    """Raise ở service layer, router tự convert sang HTTP 403."""
    pass


async def check_and_consume_quota(user: User, redis: aioredis.Redis) -> None:
    """
    PREMIUM: pass-through.
    FREE: kiểm tra và trừ quota; raise QuotaExceededError nếu hết lượt.
    """
    if user.account_tier == AccountTier.PREMIUM:
        return

    today: date = datetime.now(timezone.utc).date()

    if user.quota_reset_date != today:
        user.quota_reset_date = today
        user.daily_quota_left = settings.FREE_DAILY_QUOTA
        user.updated_at = datetime.now(timezone.utc)
        await user.save()
        await redis.delete(_quota_redis_key(str(user.id)))

    redis_key = _quota_redis_key(str(user.id))

    # Seed key atomically bằng NX — nếu key đã tồn tại (request khác vừa seed
    # hoặc đã decrement trước đó), lệnh này no-op thay vì ghi đè giá trị đang
    # có, tránh "hoàn" ngược lại quota đã bị request khác trừ.
    await redis.set(redis_key, user.daily_quota_left, ex=settings.REDIS_QUOTA_TTL, nx=True)

    # DECR trước, check kết quả sau — DECR của Redis atomic ở tầng single
    # command, nên nhiều request đồng thời sẽ không bao giờ cùng "thấy" quota
    # còn dương rồi cùng được duyệt như kiểu GET-rồi-check cũ.
    new_quota = await redis.decr(redis_key)

    if new_quota < 0:
        # Khôi phục lại để counter không tụt âm vô hạn qua nhiều request bị từ
        # chối liên tục (vd client retry dồn dập khi đã hết quota).
        await redis.incr(redis_key)
        logger.info("quota_exceeded user_id=%s", user.id)
        raise QuotaExceededError("QUOTA_EXCEEDED")

    user.daily_quota_left = new_quota
    user.updated_at = datetime.now(timezone.utc)
    await user.save()

    logger.debug("quota_consumed user_id=%s remaining=%d", user.id, new_quota)