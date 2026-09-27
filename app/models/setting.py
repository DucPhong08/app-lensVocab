from __future__ import annotations

from datetime import UTC, datetime

from beanie import Document
from pydantic import Field


class SystemSetting(Document):
    id: str = Field(default="global_config")
    free_daily_quota: int = 10
    premium_daily_quota: int = 200
    free_daily_review_cap: int = 15
    premium_daily_review_cap: int = 9999
    vision_confidence_threshold: float = 0.50
    semantic_similarity_threshold: float = 0.85
    maintenance_mode: bool = False
    max_detected_objects: int = Field(
        default=5, ge=1, le=10, description="Trần tối đa số vật thể hệ thống cho phép quét"
    )

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "system_settings"
