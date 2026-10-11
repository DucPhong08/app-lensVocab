import logging
from collections.abc import AsyncGenerator

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.vision import ScanResponse
from app.services.ai_service import BedrockOutputError
from app.services.guest_quota_service import GuestScanLimitError, consume_guest_quota
from app.services.quota_service import (
    MaintenanceModeError,
    QuotaExceededError,
    check_and_consume_quota,
)
from app.services.vision_service import _format_sse, execute_vision_scan, execute_vision_stream

logger = logging.getLogger(__name__)
router = APIRouter()
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}


async def read_scan_image(file: UploadFile) -> bytes:
    if not file.content_type or file.content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="INVALID_IMAGE_FORMAT")
    image_bytes = await file.read(MAX_IMAGE_SIZE_BYTES + 1)
    if not image_bytes:
        raise HTTPException(status_code=400, detail="FILE_EMPTY")
    if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="IMAGE_TOO_LARGE")
    return image_bytes


def scan_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MaintenanceModeError | QuotaExceededError):
        return HTTPException(
            status_code=503 if isinstance(exc, MaintenanceModeError) else 403,
            detail="MAINTENANCE_MODE"
            if isinstance(exc, MaintenanceModeError)
            else "QUOTA_EXCEEDED",
        )
    if isinstance(exc, GuestScanLimitError):
        code = str(exc)
        return HTTPException(
            status_code=503 if code in {"MAINTENANCE_MODE", "CLIENT_IP_UNAVAILABLE"} else 429,
            detail=code,
            headers={"Retry-After": "10" if code == "GUEST_SLOW_DOWN" else "3600"},
        )
    if isinstance(exc, RedisError):
        return HTTPException(status_code=503, detail="REDIS_CONNECTION_FAILED")
    if isinstance(exc, BedrockOutputError):
        return HTTPException(status_code=502, detail="AI_CONTENT_INVALID")
    if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in {
        "InvalidImageFormatException",
        "ImageTooLargeException",
    }:
        return HTTPException(status_code=400, detail="INVALID_IMAGE_DATA")
    logger.warning("scan_aws_failure type=%s", type(exc).__name__)
    return HTTPException(status_code=502, detail="AWS_VISION_UNAVAILABLE")


_SCAN_ERRORS = (
    ClientError,
    BotoCoreError,
    RedisError,
    BedrockOutputError,
    GuestScanLimitError,
    MaintenanceModeError,
    QuotaExceededError,
)


@router.post("/scan/guest", response_model=ScanResponse)
async def scan_image_guest(request: Request, file: UploadFile = File(...)) -> ScanResponse:
    image_bytes = await read_scan_image(file)
    redis = get_redis()
    try:
        await consume_guest_quota(request, redis)
        return await execute_vision_scan(image_bytes, None, redis)
    except _SCAN_ERRORS as exc:
        raise scan_http_error(exc) from exc


@router.post("/scan", response_model=ScanResponse)
async def scan_image(
    file: UploadFile = File(...), current_user: User = Depends(get_current_user)
) -> ScanResponse:
    image_bytes = await read_scan_image(file)
    redis = get_redis()
    try:
        await check_and_consume_quota(current_user, redis)
        return await execute_vision_scan(image_bytes, current_user, redis)
    except _SCAN_ERRORS as exc:
        raise scan_http_error(exc) from exc


@router.post("/scan/stream")
async def scan_image_stream(
    file: UploadFile = File(...), current_user: User = Depends(get_current_user)
) -> StreamingResponse:
    image_bytes = await read_scan_image(file)
    redis = get_redis()
    try:
        await check_and_consume_quota(current_user, redis)
    except _SCAN_ERRORS as exc:
        raise scan_http_error(exc) from exc

    async def events() -> AsyncGenerator[str, None]:
        try:
            async for event in execute_vision_stream(image_bytes, current_user, redis):
                yield event
        except _SCAN_ERRORS as exc:
            yield _format_sse("error", {"step": "ERROR", "detail": scan_http_error(exc).detail})
        except Exception:
            logger.exception("scan_stream_failed")
            yield _format_sse("error", {"step": "ERROR", "detail": "INTERNAL_SERVER_ERROR"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
