from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from app.schemas.scan_settings import ScanSettings
from app.services.ai_service import fallback_keyword_from_context

logger = logging.getLogger(__name__)


class DegradationResult(StrEnum):
    OK = "OK"  # Nhận diện tự tin cao
    FALLBACK_CONTEXT = "FALLBACK"  # Confidence thấp → học từ bối cảnh xung quanh
    UNRECOGNIZABLE = "AI_COULD_NOT_RECOGNIZE"  # Không nhận diện được


@dataclass
class VisionAnalysis:
    status: DegradationResult
    keyword: str | None = None
    confidence: float = 0.0
    message: str | None = None


_FALLBACK_MESSAGE = "Không nhận rõ vật thể chính, đề xuất học từ bối cảnh chung."
_UNRECOGNIZABLE_MESSAGE = "Không nhận diện được vật thể trong ảnh, vui lòng nhập từ vựng thủ công."


async def evaluate_vision_result(
    keyword: str | None,
    confidence: float,
    raw_description: str,
    *,
    options: ScanSettings,
) -> VisionAnalysis:
    """
    Xử lý Graceful Degradation:
    - Nếu confidence >= ngưỡng (mặc định 50%) và có keyword → OK
    - Nếu confidence thấp hoặc keyword rỗng → Gọi AWS Bedrock fallback từ raw_description
    - Nếu fallback cũng thất bại → Trả về AI_COULD_NOT_RECOGNIZE để client điều hướng nhập tay
    """
    if keyword and options.accepts_confidence(confidence):
        return VisionAnalysis(
            status=DegradationResult.OK,
            keyword=keyword.lower().strip(),
            confidence=confidence,
        )

    if not raw_description.strip():
        return VisionAnalysis(
            status=DegradationResult.UNRECOGNIZABLE,
            confidence=confidence,
            message=_UNRECOGNIZABLE_MESSAGE,
        )

    logger.info(
        "vision_low_confidence confidence=%.1f keyword=%s → kích hoạt Bedrock fallback",
        confidence,
        keyword,
    )

    try:
        fallback_kw = await fallback_keyword_from_context(raw_description)
    except Exception as exc:
        logger.error("bedrock_fallback_failed: %s", exc)
        fallback_kw = None

    if fallback_kw:
        return VisionAnalysis(
            status=DegradationResult.FALLBACK_CONTEXT,
            keyword=fallback_kw.lower().strip(),
            confidence=confidence,
            message=_FALLBACK_MESSAGE,
        )

    return VisionAnalysis(
        status=DegradationResult.UNRECOGNIZABLE,
        keyword=None,
        confidence=confidence,
        message=_UNRECOGNIZABLE_MESSAGE,
    )
