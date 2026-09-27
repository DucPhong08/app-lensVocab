from __future__ import annotations

from pydantic import BaseModel, Field


class UpdateSystemSettingRequest(BaseModel):
    free_daily_quota: int | None = Field(
        None, ge=1, le=1000, description="Hạn mức scan ngày của gói FREE"
    )
    premium_daily_quota: int | None = Field(
        None, ge=1, le=10000, description="Hạn mức scan ngày của gói PREMIUM"
    )
    free_daily_review_cap: int | None = Field(
        None, ge=1, le=500, description="Số từ ôn tối đa ngày gói FREE"
    )
    premium_daily_review_cap: int | None = Field(
        None, ge=1, le=10000, description="Số từ ôn tối đa ngày gói PREMIUM"
    )
    vision_confidence_threshold: float | None = Field(
        None, ge=0.1, le=1.0, description="Ngưỡng tin cậy của Rekognition"
    )
    semantic_similarity_threshold: float | None = Field(
        None, ge=0.5, le=1.0, description="Ngưỡng tương đồng vector cache hit"
    )
    maintenance_mode: bool | None = Field(None, description="Bật/tắt chế độ bảo trì toàn hệ thống")
    max_detected_objects: int | None = Field(
        None, ge=1, le=10, description="Trần tối đa số lượng vật thể quét trong 1 ảnh (1-10)"
    )
