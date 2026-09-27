from __future__ import annotations

from typing import Any

from app.models.models import AccountTier, UserPreferences

TIER_POLICIES: dict[AccountTier, dict[str, Any]] = {
    AccountTier.FREE: {
        "allowed_voices": ["Joanna"],
        "allowed_speeds": [1.0],
        "allow_neural_voice": False,
        "max_daily_review_goal": 20,
    },
    AccountTier.PREMIUM: {
        "allowed_voices": ["Joanna", "Matthew", "Amy", "Brian", "Olivia"],
        "allowed_speeds": [0.75, 1.0, 1.25],
        "allow_neural_voice": True,
        "max_daily_review_goal": 100,
    },
}


class TierPolicyViolation(ValueError):
    """Raise khi cấu hình vượt quá quyền lợi của gói cước hiện tại."""
    pass


def get_tier_policy(tier: AccountTier) -> dict[str, Any]:
    return TIER_POLICIES.get(tier, TIER_POLICIES[AccountTier.FREE])


def validate_user_preferences(tier: AccountTier, preferences: UserPreferences) -> None:
    policy = get_tier_policy(tier)

    if preferences.preferred_voice_id not in policy["allowed_voices"]:
        allowed = ", ".join(policy["allowed_voices"])
        raise TierPolicyViolation(
            f"Giọng đọc '{preferences.preferred_voice_id}' không thuộc quyền lợi gói {tier.value}. "
            f"Các giọng được phép: {allowed}. Nâng cấp PREMIUM để mở khóa toàn bộ giọng đọc."
        )

    if preferences.voice_speed not in policy["allowed_speeds"]:
        allowed_speeds = ", ".join(f"{s}x" for s in policy["allowed_speeds"])
        raise TierPolicyViolation(
            f"Tốc độ phát âm {preferences.voice_speed}x không thuộc gói {tier.value}. "
            f"Các tốc độ được phép: {allowed_speeds}."
        )

    if preferences.daily_review_goal > policy["max_daily_review_goal"]:
        raise TierPolicyViolation(
            f"Mục tiêu ôn tập hàng ngày tối đa cho gói {tier.value} là {policy['max_daily_review_goal']} từ."
        )
