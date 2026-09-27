from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError, ClientError

from app.models.user import User
from app.schemas.vision import BoundingBoxSchema, DetectedObjectItem, ScanResponse
from app.services.ai_service import detect_image_labels
from app.services.cache_service import FlashcardPayload, get_flashcard, stream_flashcard
from app.services.degradation_service import DegradationResult, evaluate_vision_result
from app.services.quota_service import check_and_consume_quota
from app.services.system_setting_service import get_system_settings

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)


def _format_sse(event: str, data: dict[str, Any]) -> str:
    """Format dữ liệu theo chuẩn Server-Sent Events (SSE)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def get_effective_max_objects(user: User) -> int:
    """Tính toán số vật thể tối đa dựa trên cài đặt User và trần của Admin."""
    sys_settings = await get_system_settings()
    user_max = (
        user.preferences.max_detected_objects
        if user.preferences and hasattr(user.preferences, "max_detected_objects")
        else 5
    )
    admin_max = getattr(sys_settings, "max_detected_objects", 5)
    return min(user_max, admin_max)


async def execute_vision_scan(
    image_bytes: bytes,
    current_user: User,
    redis: Redis,
) -> ScanResponse:
    """
    Toàn bộ nghiệp vụ quét ảnh:
      1. Khấu trừ quota (Redis atomic)
      2. Giới hạn số vật thể theo trần Admin
      3. Nhận diện qua AWS Rekognition
      4. Graceful Degradation (Bedrock Fallback nếu < 50%)
      5. Tra cứu Cache đa tầng / Sinh Bedrock & Polly
    """
    await check_and_consume_quota(current_user, redis)

    effective_max = await get_effective_max_objects(current_user)

    vision_result = await detect_image_labels(image_bytes, max_labels=effective_max)

    # Danh sách tất cả vật thể Rekognition phát hiện được
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

    candidate_keywords = vision_result.top_parents + vision_result.top_aliases
    payload: FlashcardPayload = await get_flashcard(
        keyword=analysis.keyword,
        redis=redis,
        candidate_keywords=candidate_keywords,
    )

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


async def execute_vision_stream(
    image_bytes: bytes,
    current_user: User,
    redis: Redis,
) -> AsyncGenerator[str, None]:
    """Generator phát sự kiện Server-Sent Events (SSE) theo tiến trình nhận diện."""
    effective_max = await get_effective_max_objects(current_user)

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

    # Tra cứu Cache & Bedrock/Polly
    candidate_keywords = vision_result.top_parents + vision_result.top_aliases
    async for chunk in stream_flashcard(
        analysis.keyword, redis, candidate_keywords=candidate_keywords
    ):
        yield _format_sse(chunk["event"], chunk["data"])
