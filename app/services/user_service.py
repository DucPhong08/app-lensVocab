from __future__ import annotations

from app.models.user import User, UserPreferences
from app.schemas.user import UpdatePreferencesRequest, UserPreferencesResponse
from app.services.tier_service import resolve_tier_policy, validate_user_preferences


def read_preferences(current_user: User) -> UserPreferencesResponse:
    """Lấy thiết lập học tập cá nhân và danh sách quyền lợi theo gói cước."""
    policy = resolve_tier_policy(current_user.account_tier)
    return UserPreferencesResponse(
        preferences=current_user.preferences,
        account_tier=current_user.account_tier,
        allowed_voices=policy["allowed_voices"],
        allowed_speeds=policy["allowed_speeds"],
        allow_neural_voice=policy["allow_neural_voice"],
        max_daily_review_goal=policy["max_daily_review_goal"],
    )


async def update_preferences(
    current_user: User, body: UpdatePreferencesRequest
) -> UserPreferencesResponse:
    """Cập nhật thiết lập học tập cá nhân và kiểm tra quyền lợi gói cước."""
    current_pref = current_user.preferences.model_dump()
    update_data = body.model_dump(exclude_unset=True)

    updated_dict = {**current_pref, **update_data}
    new_pref = UserPreferences(**updated_dict)

    # Validate gói cước (ném TierPolicyViolation nếu vượt quyền)
    validate_user_preferences(current_user.account_tier, new_pref)

    current_user.preferences = new_pref
    await current_user.save()

    policy = resolve_tier_policy(current_user.account_tier)
    return UserPreferencesResponse(
        preferences=current_user.preferences,
        account_tier=current_user.account_tier,
        allowed_voices=policy["allowed_voices"],
        allowed_speeds=policy["allowed_speeds"],
        allow_neural_voice=policy["allow_neural_voice"],
        max_daily_review_goal=policy["max_daily_review_goal"],
    )
