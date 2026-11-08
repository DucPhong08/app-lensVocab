from __future__ import annotations

from app.config import settings
from app.models.user import User, UserPreferences
from app.schemas.user import UpdatePreferencesRequest, UserPreferencesResponse
from app.services.scan_settings_service import scan_settings
from app.services.system_setting_service import fetch_system_settings
from app.services.tier_service import resolve_tier_policy, review_goal, validate_user_preferences


async def read_preferences(current_user: User) -> UserPreferencesResponse:
    """Lấy thiết lập học tập cá nhân và danh sách quyền lợi theo gói cước."""
    policy = resolve_tier_policy(current_user.account_tier)
    system_settings = await fetch_system_settings()
    return UserPreferencesResponse(
        preferences=current_user.preferences.model_copy(
            update={"preferred_voice_id": settings.POLLY_VOICE_ID}
        ),
        account_tier=current_user.account_tier,
        allowed_voices=policy["allowed_voices"],
        allowed_speeds=policy["allowed_speeds"],
        allow_neural_voice=policy["allow_neural_voice"],
        max_daily_review_goal=policy["max_daily_review_goal"],
        effective_daily_review_goal=review_goal(current_user, system_settings),
        scan_settings=scan_settings(current_user, system_settings),
    )


async def update_preferences(
    current_user: User, body: UpdatePreferencesRequest
) -> UserPreferencesResponse:
    """Cập nhật thiết lập học tập cá nhân và kiểm tra quyền lợi gói cước."""
    current_pref = {
        **current_user.preferences.model_dump(),
        "preferred_voice_id": settings.POLLY_VOICE_ID,
    }
    update_data = body.model_dump(exclude_unset=True)

    updated_dict = {**current_pref, **update_data}
    new_pref = UserPreferences(**updated_dict)

    # Validate gói cước (ném TierPolicyViolation nếu vượt quyền)
    validate_user_preferences(current_user.account_tier, new_pref)

    current_user.preferences = new_pref
    await current_user.set({"preferences": new_pref.model_dump()})

    return await read_preferences(current_user)
