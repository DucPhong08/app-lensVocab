from __future__ import annotations

from app.schemas.admin import UpdateSystemSettingRequest
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserMeResponse
from app.schemas.flashcard import ConfirmFlashcardRequest, FlashcardItemResponse
from app.schemas.review import ReviewCardResponse, SubmitReviewRequest, SubmitReviewResponse
from app.schemas.user import UpdatePreferencesRequest, UserPreferencesResponse
from app.schemas.vision import BoundingBoxSchema, DetectedObjectItem, ScanResponse

__all__ = [
    "RegisterRequest",
    "LoginRequest",
    "TokenResponse",
    "UserMeResponse",
    "UpdatePreferencesRequest",
    "UserPreferencesResponse",
    "UpdateSystemSettingRequest",
    "ConfirmFlashcardRequest",
    "FlashcardItemResponse",
    "ReviewCardResponse",
    "SubmitReviewRequest",
    "SubmitReviewResponse",
    "BoundingBoxSchema",
    "DetectedObjectItem",
    "ScanResponse",
]
