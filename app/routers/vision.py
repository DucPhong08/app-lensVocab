from __future__ import annotations

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from app.dependencies.auth import get_current_user
from app.models.models import User
from app.redis_client import get_redis
from app.services.ai_service import detect_image_labels
from app.services.cache_service import FlashcardPayload, resolve_flashcard
from app.services.degradation_service import DegradationResult, handle_vision_result
from app.services.quota_service import MaintenanceModeError, QuotaExceededError, check_and_consume_quota

router = APIRouter()

MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB giới hạn direct bytes của AWS Rekognition
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}


class BoundingBoxSchema(BaseModel):
    width: float   # Chiều rộng khung (tỉ lệ 0.0 - 1.0)
    height: float  # Chiều cao khung (tỉ lệ 0.0 - 1.0)
    left: float    # Tọa độ X góc trên bên trái (tỉ lệ 0.0 - 1.0)
    top: float     # Tọa độ Y góc trên bên trái (tỉ lệ 0.0 - 1.0)


class ScanResponse(BaseModel):
    status: str                                        # "OK" | "FALLBACK" | "AI_COULD_NOT_RECOGNIZE"
    keyword: str | None = None                         # Từ tiếng Anh: "chair"
    pronunciation: str | None = None                   # Phiên âm IPA: "/tʃer/"
    meaning_vi: str | None = None                      # Nghĩa tiếng Việt: "cái ghế"
    example_1: str | None = None                       # Ví dụ 1: "I sit on a chair"
    example_2: str | None = None                       # Ví dụ 2: "This wooden chair is sturdy"
    related_words: list[str] = []                      # Từ liên quan: ["seat", "sofa", "stool"]
    aliases: list[str] = []                            # Tên gọi khác từ Rekognition: ["Seat"]
    categories: list[str] = []                         # Danh mục: ["Furniture"]
    parents: list[str] = []                            # Danh mục cha: ["Furniture", "Home Decor"]
    bounding_box: BoundingBoxSchema | None = None      # Tọa độ để Frontend vẽ khung viền (Google Lens style)
    audio_base64: str | None = None                    # MP3 audio từ AWS Polly
    confidence: float = 0.0
    source: str | None = None                          # "redis" | "mongodb" | "bedrock"
    is_draft: bool = False
    message: str | None = None


@router.post("/scan", response_model=ScanResponse)
async def scan_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> ScanResponse:
    """
    POST /api/v1/scan

    Pipeline thuần AWS:
      0. Xác thực người dùng và kiểm tra quota hàng ngày.
      1. Nhận file ảnh từ client.
      2. AWS Rekognition detect_labels:
         - Bóc tách nhãn, độ tin cậy, categories, aliases, parents.
         - Trích xuất BoundingBox tọa độ đồ vật cụ thể (Instances).
         - Thuật toán Smart Selection: ưu tiên thực thể cụ thể trước chất liệu/bối cảnh.
      3. Graceful Degradation check (nếu confidence < 50% → Bedrock fallback).
      4. Multi-Tier Cache:
         - Tier 1: Redis exact match
         - Tier 2: MongoDB Atlas Vector Search (Bedrock Titan Embeddings)
         - Tier 3: AWS Bedrock (Nova sinh IPA, ví dụ, từ liên quan) + AWS Polly (đọc MP3)
      5. Trả về đầy đủ dữ liệu cho client hiển thị, vẽ khung viền và phát âm.
    """
    redis = get_redis()

    # ── 0. Kiểm tra & Trừ Quota ngày của User ─────────────────────────────────
    try:
        await check_and_consume_quota(current_user, redis)
    except MaintenanceModeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MAINTENANCE_MODE",
        )
    except QuotaExceededError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="QUOTA_EXCEEDED",
        )

    if file.content_type and file.content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_IMAGE_FORMAT",
        )

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="FILE_EMPTY",
        )

    if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="IMAGE_TOO_LARGE",
        )

    # ── 1. AWS Rekognition: Nhận diện đồ vật + BoundingBox + Metadata ──────────
    try:
        vision_result = await detect_image_labels(image_bytes)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("InvalidImageFormatException", "ImageTooLargeException"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="INVALID_IMAGE_DATA",
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AWS_VISION_UNAVAILABLE",
        )

    # ── 2. Graceful Degradation ───────────────────────────────────────────────
    analysis = await handle_vision_result(
        keyword=vision_result.top_label,
        confidence=vision_result.top_confidence,
        raw_description=vision_result.raw_description,
    )

    if analysis.status == DegradationResult.UNRECOGNIZABLE:
        return ScanResponse(
            status=analysis.status.value,
            keyword=None,
            confidence=analysis.confidence,
            message=analysis.message,
        )

    # ── 3. Multi-Tier Cache (Bedrock + Polly + MongoDB Atlas) ─────────────────
    payload: FlashcardPayload = await resolve_flashcard(
        keyword=analysis.keyword,
        redis=redis,
    )

    # Map BoundingBox sang Schema nếu có
    box_schema: BoundingBoxSchema | None = None
    if vision_result.top_bounding_box:
        box_schema = BoundingBoxSchema(
            width=vision_result.top_bounding_box.width,
            height=vision_result.top_bounding_box.height,
            left=vision_result.top_bounding_box.left,
            top=vision_result.top_bounding_box.top,
        )

    return ScanResponse(
        status=analysis.status.value,
        keyword=payload.keyword,
        pronunciation=payload.pronunciation,
        meaning_vi=payload.meaning_vi,
        example_1=payload.example_1,
        example_2=payload.example_2,
        related_words=payload.related_words,
        aliases=vision_result.top_aliases,
        categories=vision_result.top_categories,
        parents=vision_result.top_parents,
        bounding_box=box_schema,
        audio_base64=payload.audio_base64,
        confidence=analysis.confidence,
        source=payload.source,
        is_draft=payload.is_draft,
        message=analysis.message,
    )
