from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.models.models import SystemSetting

logger = logging.getLogger(__name__)

_cached_setting: SystemSetting | None = None


def _build_default_system_setting() -> SystemSetting:
    return SystemSetting.model_construct(
        id="global_config",
        free_daily_quota=settings.FREE_DAILY_QUOTA,
        premium_daily_quota=200,
        free_daily_review_cap=settings.REVIEW_DAILY_CAP,
        premium_daily_review_cap=9999,
        vision_confidence_threshold=settings.VISION_CONFIDENCE_THRESHOLD,
        semantic_similarity_threshold=settings.SEMANTIC_SIMILARITY_THRESHOLD,
        maintenance_mode=False,
        updated_at=datetime.now(timezone.utc),
    )


async def get_system_settings() -> SystemSetting:
    """Lấy cài đặt hệ thống động từ MongoDB Atlas (Singleton 'global_config').
    
    Fallback an toàn về giá trị từ .env/Settings nếu MongoDB chưa khởi tạo
    hoặc chưa có record.
    """
    global _cached_setting

    try:
        doc = await SystemSetting.get("global_config")
        if doc is not None:
            _cached_setting = doc
            return doc

        # Tạo singleton record lần đầu trong DB
        doc = _build_default_system_setting()
        try:
            await doc.insert()
            _cached_setting = doc
            return doc
        except Exception:
            return doc
    except Exception as exc:
        logger.debug("system_setting_fetch_fallback_to_default: %s", exc)
        if _cached_setting is not None:
            return _cached_setting
        return _build_default_system_setting()


async def update_system_settings(updates: dict[str, Any]) -> SystemSetting:
    """Cập nhật cài đặt hệ thống của Quản trị viên."""
    global _cached_setting
    doc = await get_system_settings()

    allowed_fields = {
        "free_daily_quota",
        "premium_daily_quota",
        "free_daily_review_cap",
        "premium_daily_review_cap",
        "vision_confidence_threshold",
        "semantic_similarity_threshold",
        "maintenance_mode",
    }

    for key, value in updates.items():
        if key in allowed_fields and hasattr(doc, key):
            setattr(doc, key, value)

    doc.updated_at = datetime.now(timezone.utc)
    try:
        await doc.save()
    except Exception as exc:
        logger.warning("system_setting_save_to_db_failed: %s", exc)

    _cached_setting = doc
    return doc
