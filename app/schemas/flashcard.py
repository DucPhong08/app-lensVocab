from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.models.flashcard import FlashcardStatus


class ConfirmFlashcardRequest(BaseModel):
    # Legacy content fields are ignored: only the server owns shared vocabulary.
    keyword: Annotated[
        str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=100)
    ]


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


class FlashcardText(BaseModel):
    """Mongo projection: deliberately excludes stored audio and embeddings."""

    id: uuid.UUID = Field(alias="_id")
    keyword: str
    pronunciation: str | None = None
    meaning_vi: str
    example_1: str
    example_2: str
    related_words: list[str] = []


class FlashcardPageResponse(BaseModel):
    items: list[FlashcardItemResponse]
    total: int
    next_offset: int | None


class FlashcardAudioResponse(BaseModel):
    audio_base64: str | None = None
