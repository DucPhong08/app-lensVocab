from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.dependencies.auth import get_current_user
from app.models.models import SystemSetting, User
from app.services.system_setting_service import get_system_settings, update_system_settings

router = APIRouter()


class UpdateSystemSettingRequest(BaseModel):
    free_daily_quota: Optional[int] = Field(
        None, ge=1, le=1000, description="Hạn mức scan ngày của gói FREE"
    )
    premium_daily_quota: Optional[int] = Field(
        None, ge=1, le=10000, description="Hạn mức scan ngày của gói PREMIUM"
    )
    free_daily_review_cap: Optional[int] = Field(
        None, ge=1, le=500, description="Số từ ôn tối đa ngày gói FREE"
    )
    premium_daily_review_cap: Optional[int] = Field(
        None, ge=1, le=10000, description="Số từ ôn tối đa ngày gói PREMIUM"
    )
    vision_confidence_threshold: Optional[float] = Field(
        None, ge=0.1, le=1.0, description="Ngưỡng tin cậy của Rekognition"
    )
    semantic_similarity_threshold: Optional[float] = Field(
        None, ge=0.5, le=1.0, description="Ngưỡng tương đồng vector cache hit"
    )
    maintenance_mode: Optional[bool] = Field(
        None, description="Bật/tắt chế độ bảo trì toàn hệ thống"
    )
    max_detected_objects: Optional[int] = Field(
        None, ge=1, le=10, description="Trần tối đa số lượng vật thể quét trong 1 ảnh (1-10)"
    )


@router.get("/admin/settings", response_model=SystemSetting)
async def get_admin_settings(
    current_user: User = Depends(get_current_user),
) -> SystemSetting:
    """Lấy cấu hình hệ thống động hiện tại."""
    return await get_system_settings()


@router.patch("/admin/settings", response_model=SystemSetting)
async def update_admin_settings(
    body: UpdateSystemSettingRequest,
    current_user: User = Depends(get_current_user),
) -> SystemSetting:
    """
    Cập nhật cài đặt hệ thống động mà không cần restart server hoặc redeploy.
    Áp dụng ngay lập tức cho toàn bộ các luồng Quota, AI Thresholds và Bảo trì.
    """
    updates = body.model_dump(exclude_unset=True)
    return await update_system_settings(updates)
