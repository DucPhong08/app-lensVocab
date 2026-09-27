from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies.auth import get_current_user
from app.models.setting import SystemSetting
from app.models.user import User
from app.schemas.admin import UpdateSystemSettingRequest
from app.services.system_setting_service import get_system_settings, update_system_settings

router = APIRouter()


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
