import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.vision import BoundingBoxSchema, DetectedObjectItem, ScanResponse
from app.services.ai_service import detect_image_labels
from app.services.cache_service import (
    FlashcardPayload,
    get_flashcard,
    stream_flashcard,
)
from app.services.degradation_service import DegradationResult, evaluate_vision_result
from app.services.quota_service import (
    MaintenanceModeError,
    QuotaExceededError,
    check_and_consume_quota,
)
from app.services.system_setting_service import fetch_system_settings

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB giới hạn direct bytes của AWS Rekognition
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}


@router.post("/scan", response_model=ScanResponse)
async def scan_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> ScanResponse:
    """
    POST /vision/scan

    Pipeline thuần AWS:
      0. Xác thực người dùng và kiểm tra quota hàng ngày.
      1. Nhận file ảnh từ client.
      2. AWS Rekognition detect_labels:
         - Bóc tách nhãn, độ tin cậy, categories, aliases, parents.
         - Trích xuất BoundingBox tọa độ đồ vật cụ thể (Instances).
         - Thuật toán Smart Selection: ưu tiên thực thể cụ thể trước chất liệu/bối cảnh.
      3. Graceful Degradation check (nếu confidence < 50% → Bedrock fallback).
      4. Cache Resolver (Exact Match — không dùng Vector Search để tránh sai lệch ngữ nghĩa):
         - Tier 1: Redis exact match (keyword chính, rồi duyệt Parents/Aliases từ Rekognition)
         - Tier 2: MongoDB exact match (keyword chính, rồi duyệt Parents/Aliases từ Rekognition)
         - Cache Miss: AWS Bedrock (Nova sinh IPA, ví dụ, từ liên quan) + AWS Polly (đọc MP3)
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
    except RedisError as exc:
        logger.error("redis_connection_failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="REDIS_CONNECTION_FAILED",
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

    # ── 1. Tính toán giới hạn số vật thể (User preference vs Admin ceiling) ───
    sys_settings = await fetch_system_settings()
    user_max = (
        current_user.preferences.max_detected_objects
        if current_user.preferences and hasattr(current_user.preferences, "max_detected_objects")
        else 5
    )
    admin_max = getattr(sys_settings, "max_detected_objects", 5)
    effective_max = min(user_max, admin_max)

    # ── 2. AWS Rekognition: Nhận diện đồ vật + BoundingBox + Metadata ──────────
    try:
        vision_result = await detect_image_labels(image_bytes, max_labels=effective_max)
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
    except BotoCoreError as exc:
        logger.error("aws_rekognition_failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AWS_CONNECTION_OR_CREDENTIALS_ERROR: {exc.__class__.__name__}",
        )

    # Danh sách tất cả vật thể Rekognition phát hiện được (capped bởi effective_max)
    detected_objects = [
        DetectedObjectItem(
            keyword=lbl.name,
            confidence=round(lbl.confidence, 2),
            bounding_box=BoundingBoxSchema(
                width=lbl.bounding_box.width,
                height=lbl.bounding_box.height,
                left=lbl.bounding_box.left,
                top=lbl.bounding_box.top,
            )
            if lbl.bounding_box
            else None,
            categories=lbl.categories,
            aliases=lbl.aliases,
            parents=lbl.parents,
        )
        for lbl in vision_result.labels[:effective_max]
    ]

    # ── 3. Graceful Degradation ───────────────────────────────────────────────
    analysis = await evaluate_vision_result(
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
            detected_objects=detected_objects,
        )

    # ── 4. Cache Resolver: Exact Match (keyword chính + Parents/Aliases) ───────
    candidate_keywords = vision_result.top_parents + vision_result.top_aliases
    try:
        payload: FlashcardPayload = await get_flashcard(
            keyword=analysis.keyword,
            redis=redis,
            candidate_keywords=candidate_keywords,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.error("aws_generation_failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AWS_BEDROCK_OR_POLLY_ERROR: {exc.__class__.__name__}",
        )
    except RedisError as exc:
        logger.error("redis_cache_failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="REDIS_CONNECTION_FAILED",
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
        detected_objects=detected_objects,
        audio_base64=payload.audio_base64,
        confidence=analysis.confidence,
        source=payload.source,
        is_draft=payload.is_draft,
        message=analysis.message,
    )


def _format_sse(event: str, data: dict[str, Any]) -> str:
    """Format dữ liệu theo chuẩn Server-Sent Events (SSE)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/scan/stream")
async def scan_image_stream(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """POST /vision/scan/stream (Server-Sent Events)

    Stream tiến trình nhận diện hình ảnh và tạo Flashcard theo thời gian thực:
      - event: status (Bắt đầu xử lý, tra cứu cache, gọi AI...)
      - event: vision_detected (Nhận diện nhãn + Bounding Box ngay từ AWS Rekognition)
      - event: vocab_content (Nghĩa tiếng Việt, IPA, câu ví dụ khi tìm thấy hoặc Bedrock sinh xong)
      - event: audio_ready (File âm thanh MP3 từ AWS Polly)
      - event: done (Hoàn tất chu trình)
    """
    redis = get_redis()

    # ── 0. Kiểm tra Quota & File ──────────────────────────────────────────────
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
    except RedisError as exc:
        logger.error("redis_stream_quota_failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="REDIS_CONNECTION_FAILED",
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

    async def event_generator() -> AsyncGenerator[str, None]:
        # 0. Giới hạn số vật thể nhận diện (User setting vs Admin ceiling)
        sys_settings = await fetch_system_settings()
        user_max = (
            current_user.preferences.max_detected_objects
            if current_user.preferences
            and hasattr(current_user.preferences, "max_detected_objects")
            else 5
        )
        admin_max = getattr(sys_settings, "max_detected_objects", 5)
        effective_max = min(user_max, admin_max)

        # 1. Báo bắt đầu quét ảnh
        yield _format_sse(
            "status",
            {"step": "START", "message": "Đang phân tích hình ảnh qua AWS Rekognition..."},
        )

        try:
            vision_result = await detect_image_labels(image_bytes, max_labels=effective_max)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("InvalidImageFormatException", "ImageTooLargeException"):
                yield _format_sse("error", {"step": "ERROR", "detail": "INVALID_IMAGE_DATA"})
                return
            yield _format_sse("error", {"step": "ERROR", "detail": "AWS_VISION_UNAVAILABLE"})
            return
        except BotoCoreError as exc:
            logger.error("aws_stream_rekognition_failed: %s", exc)
            yield _format_sse(
                "error",
                {"step": "ERROR", "detail": f"AWS_ERROR: {exc.__class__.__name__}"},
            )
            return

        # 2. Xử lý độ tin cậy và suy thoái
        analysis = await evaluate_vision_result(
            keyword=vision_result.top_label,
            confidence=vision_result.top_confidence,
            raw_description=vision_result.raw_description,
        )

        box_data: dict[str, float] | None = None
        if vision_result.top_bounding_box:
            box_data = {
                "width": vision_result.top_bounding_box.width,
                "height": vision_result.top_bounding_box.height,
                "left": vision_result.top_bounding_box.left,
                "top": vision_result.top_bounding_box.top,
            }

        detected_objects = [
            {
                "keyword": lbl.name,
                "confidence": round(lbl.confidence, 2),
                "bounding_box": {
                    "width": lbl.bounding_box.width,
                    "height": lbl.bounding_box.height,
                    "left": lbl.bounding_box.left,
                    "top": lbl.bounding_box.top,
                }
                if lbl.bounding_box
                else None,
                "categories": lbl.categories,
                "aliases": lbl.aliases,
                "parents": lbl.parents,
            }
            for lbl in vision_result.labels[:effective_max]
        ]

        # Bắn ngay event nhận diện nhãn + Bounding Box để Frontend vẽ khung trước mắt người dùng
        yield _format_sse(
            "vision_detected",
            {
                "step": "VISION_DETECTED",
                "status": analysis.status.value,
                "keyword": analysis.keyword,
                "confidence": analysis.confidence,
                "bounding_box": box_data,
                "categories": vision_result.top_categories,
                "aliases": vision_result.top_aliases,
                "parents": vision_result.top_parents,
                "detected_objects": detected_objects,
                "message": analysis.message,
            },
        )

        if analysis.status == DegradationResult.UNRECOGNIZABLE:
            yield _format_sse("done", {"step": "DONE", "status": "UNRECOGNIZABLE"})
            return

        # 3. Stream tiến trình tra cứu Cache & Bedrock/Polly
        candidate_keywords = vision_result.top_parents + vision_result.top_aliases
        async for chunk in stream_flashcard(
            analysis.keyword, redis, candidate_keywords=candidate_keywords
        ):
            yield _format_sse(chunk["event"], chunk["data"])

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
