from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.models.flashcard import FlashcardStatus


class ConfirmFlashcardRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "keyword": "chair",
                "pronunciation": "/tʃer/",
                "meaning_vi": "cái ghế",
                "example_1": "I sit on a chair.",
                "example_2": "This wooden chair is sturdy.",
                "related_words": ["seat", "sofa", "stool"],
                "audio_base64": None,
            }
        }
    }

    keyword: str
    pronunciation: str | None = None
    meaning_vi: str
    example_1: str
    example_2: str
    related_words: list[str] = []
    audio_base64: str | None = None


class FlashcardItemResponse(BaseModel):
    user_flashcard_id: uuid.UUID
    global_flashcard_id: uuid.UUID
    keyword: str
    pronunciation: str | None
    meaning_vi: str
    example_1: str
    example_2: str
    related_words: list[str]
    audio_base64: str | None
    status: FlashcardStatus
    interval: int
    repetitions: int
    efactor: float
    next_review_date: str
