from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError, ClientError

from app.models.setting import SystemSetting
from app.models.user import User
from app.schemas.vision import BoundingBoxSchema, ScanResponse
from app.services.ai_service import detect_image_labels
from app.services.cache_service import get_flashcard, stream_flashcard
from app.services.degradation_service import DegradationResult, evaluate_vision_result
from app.services.system_setting_service import fetch_system_settings
from app.services.vision_selection_service import detected_objects as scan_objects

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)


def _format_sse(event: str, data: dict[str, Any]) -> str:
    """Format dữ liệu theo chuẩn Server-Sent Events (SSE)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def resolve_max_objects(
    user: User | None, system_settings: SystemSetting | None = None
) -> int:
    """Tính toán số vật thể tối đa, áp dụng trần hệ thống cho cả khách."""
    sys_settings = system_settings if system_settings is not None else await fetch_system_settings()
    admin_max = getattr(sys_settings, "max_detected_objects", 5)
    if user is None:
        return min(5, admin_max)
    user_max = (
        user.preferences.max_detected_objects
        if user.preferences and hasattr(user.preferences, "max_detected_objects")
        else 5
    )
    return min(user_max, admin_max)


async def _analyze_image(
    image_bytes: bytes, user: User | None, system_settings: SystemSetting
) -> ScanResponse:
    limit = await resolve_max_objects(user, system_settings)
    result = await detect_image_labels(image_bytes, max_labels=limit)
    objects = scan_objects(result, limit, user)
    analysis = await evaluate_vision_result(
        keyword=result.top_label,
        confidence=result.top_confidence,
        raw_description=result.raw_description,
        system_settings=system_settings,
    )
    box = None
    if analysis.status == DegradationResult.OK and result.top_bounding_box:
        box = BoundingBoxSchema(
            width=result.top_bounding_box.width,
            height=result.top_bounding_box.height,
            left=result.top_bounding_box.left,
            top=result.top_bounding_box.top,
        )
    selected_id = (
        next(
            (
                item.object_id
                for item in objects
                if item.keyword.casefold() == (result.top_label or "").casefold()
                and item.bounding_box is not None
            ),
            None,
        )
        if box
        else None
    )
    return ScanResponse(
        status=analysis.status.value,
        selected_object_id=selected_id,
        keyword=analysis.keyword,
        confidence=analysis.confidence,
        message=analysis.message,
        bounding_box=box,
        detected_objects=objects,
        categories=result.top_categories,
        aliases=result.top_aliases,
        parents=result.top_parents,
    )


async def execute_vision_scan(
    image_bytes: bytes,
    current_user: User | None,
    redis: Redis,
    *,
    system_settings: SystemSetting | None = None,
) -> ScanResponse:
    system_settings = (
        system_settings if system_settings is not None else await fetch_system_settings()
    )
    scan = await _analyze_image(image_bytes, current_user, system_settings)
    if scan.status == DegradationResult.UNRECOGNIZABLE:
        return scan
    payload = await get_flashcard(
        keyword=scan.keyword,
        redis=redis,
        candidate_keywords=scan.parents + scan.aliases,
    )
    return ScanResponse(**{**scan.model_dump(), **payload.to_dict()})


async def execute_vision_stream(
    image_bytes: bytes,
    current_user: User,
    redis: Redis,
    *,
    system_settings: SystemSetting | None = None,
) -> AsyncGenerator[str, None]:
    """Generator phát sự kiện Server-Sent Events (SSE) theo tiến trình nhận diện."""
    system_settings = (
        system_settings if system_settings is not None else await fetch_system_settings()
    )
    # 1. Báo bắt đầu quét ảnh
    yield _format_sse(
        "status",
        {"step": "START", "message": "Đang phân tích hình ảnh qua AWS Rekognition..."},
    )

    try:
        scan = await _analyze_image(image_bytes, current_user, system_settings)
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

    yield _format_sse(
        "vision_detected",
        {
            "step": "VISION_DETECTED",
            **scan.model_dump(
                include={
                    "status",
                    "selected_object_id",
                    "keyword",
                    "confidence",
                    "bounding_box",
                    "categories",
                    "aliases",
                    "parents",
                    "detected_objects",
                    "message",
                }
            ),
        },
    )

    if scan.status == DegradationResult.UNRECOGNIZABLE:
        yield _format_sse("done", {"step": "DONE", "status": "UNRECOGNIZABLE"})
        return

    # Tra cứu Cache & Bedrock/Polly
    candidate_keywords = scan.parents + scan.aliases
    async for chunk in stream_flashcard(scan.keyword, redis, candidate_keywords=candidate_keywords):
        yield _format_sse(chunk["event"], chunk["data"])
