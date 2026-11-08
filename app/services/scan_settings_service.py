from app.models.setting import SystemSetting
from app.models.user import User, UserPreferences
from app.schemas.scan_settings import ScanSettings


def scan_settings(user: User | None, system_settings: SystemSetting) -> ScanSettings:
    preferences = user.preferences if user and user.preferences else UserPreferences()
    threshold = system_settings.vision_confidence_threshold
    # Keep the legacy percentage form consistent with Cloud degradation checks.
    threshold = threshold / 100 if threshold > 1 else threshold
    return ScanSettings(
        max_detected_objects=min(
            preferences.max_detected_objects, system_settings.max_detected_objects
        ),
        max_allowed_objects=system_settings.max_detected_objects,
        confidence_threshold=max(0, min(1, threshold)),
    )
