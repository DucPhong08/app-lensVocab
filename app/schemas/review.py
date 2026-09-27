from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ReviewCardResponse(BaseModel):
    user_flashcard_id: uuid.UUID
    keyword: str
    pronunciation: str | None
    meaning_vi: str
    example_1: str
    example_2: str
    related_words: list[str]
    audio_base64: str | None
    interval: int
    repetitions: int
    efactor: float
    next_review_date: str


class SubmitReviewRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "quality": 4,
            }
        }
    }

    quality: int = Field(
        ...,
        ge=0,
        le=5,
        description="Đánh giá từ 0 (quên hoàn toàn) đến 5 (nhớ hoàn hảo)",
        examples=[4],
    )


class SubmitReviewResponse(BaseModel):
    user_flashcard_id: uuid.UUID
    interval_after: int
    repetitions_after: int
    efactor_after: float
    next_review_date: str
    message: str
