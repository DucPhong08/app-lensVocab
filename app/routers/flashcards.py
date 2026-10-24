from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.flashcard import (
    CardImageResponse,
    ConfirmFlashcardRequest,
    CropBox,
    FlashcardAudioResponse,
    FlashcardItemResponse,
    FlashcardPageResponse,
)
from app.services.card_image_service import (
    MAX_IMAGE_BYTES,
    CardImageError,
    delete_card_image,
    get_card_image,
    set_card_image,
)
from app.services.flashcard_service import (
    CardAudioNotFoundError,
    DraftExpiredError,
    confirm_card,
    get_card_audio,
    list_card_page,
    list_cards,
)

router = APIRouter()


@router.post("/flashcards/confirm", response_model=FlashcardItemResponse)
async def confirm_flashcard(
    body: ConfirmFlashcardRequest,
    current_user: User = Depends(get_current_user),
) -> FlashcardItemResponse:
    """
    POST /flashcards/confirm
    Xác nhận thẻ nháp sau khi scan:
      1. Lưu vào GlobalFlashcard (MongoDB Atlas) + đồng bộ Redis.
      2. Tạo bản ghi UserFlashcard cho người dùng với trạng thái CONFIRMED.
    """
    redis = get_redis()
    try:
        return await confirm_card(current_user.id, body, redis)
    except DraftExpiredError as exc:
        raise HTTPException(status_code=409, detail="DRAFT_EXPIRED") from exc
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="REDIS_CONNECTION_FAILED") from exc


@router.get("/flashcards", response_model=list[FlashcardItemResponse])
async def list_user_flashcards(
    current_user: User = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[FlashcardItemResponse]:
    """
    GET /flashcards
    Legacy: trả một trang giới hạn, không kèm âm thanh.
    """
    return await list_cards(current_user.id, limit=limit, offset=offset)


@router.get("/flashcards/page", response_model=FlashcardPageResponse)
async def get_flashcard_page(
    current_user: User = Depends(get_current_user),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> FlashcardPageResponse:
    return await list_card_page(current_user.id, limit=limit, offset=offset)


@router.get("/flashcards/{user_flashcard_id}/audio", response_model=FlashcardAudioResponse)
async def get_flashcard_audio(
    user_flashcard_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
) -> FlashcardAudioResponse:
    try:
        return await get_card_audio(current_user.id, user_flashcard_id)
    except CardAudioNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/flashcards/{user_flashcard_id}/image", response_model=CardImageResponse)
async def upload_flashcard_image(
    user_flashcard_id: uuid.UUID,
    file: UploadFile = File(...),
    crop: str | None = Form(None, max_length=512),
    style: Literal["photo", "sticker"] = Form("photo"),
    current_user: User = Depends(get_current_user),
) -> CardImageResponse:
    if (file.content_type or "").lower() not in {"image/jpeg", "image/png"}:
        raise HTTPException(status_code=400, detail="INVALID_IMAGE_FORMAT")
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="IMAGE_TOO_LARGE")
    try:
        box = CropBox.model_validate_json(crop) if crop else None
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="INVALID_CROP_BOX") from exc
    try:
        return await set_card_image(current_user.id, user_flashcard_id, data, box, style=style)
    except CardImageError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/flashcards/{user_flashcard_id}/image", response_model=CardImageResponse)
async def flashcard_image(
    user_flashcard_id: uuid.UUID, current_user: User = Depends(get_current_user)
) -> CardImageResponse:
    try:
        return await get_card_image(current_user.id, user_flashcard_id)
    except CardImageError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.delete("/flashcards/{user_flashcard_id}/image", response_model=CardImageResponse)
async def remove_flashcard_image(
    user_flashcard_id: uuid.UUID, current_user: User = Depends(get_current_user)
) -> CardImageResponse:
    try:
        return await delete_card_image(current_user.id, user_flashcard_id)
    except CardImageError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
