from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from app.config import settings
from app.services.ai_service import fallback_keyword_from_context

logger = logging.getLogger(__name__)


class DegradationResult(str, Enum):
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


def _normalize_threshold_percent(threshold: float) -> float:
    """settings.VISION_CONFIDENCE_THRESHOLD có thể được cấu hình dạng phân số
    (0.50) hoặc phần trăm (50) tùy người config, quy về thang 0-100 để so sánh
    trực tiếp với confidence của Rekognition.

    Lưu ý: cách đoán này chỉ đáng tin với khoảng giá trị hợp lý cho một ngưỡng
    nhận diện. Nếu set đúng 1.0, hàm coi là 100% (ngưỡng cao nhất) — nếu ý định
    thực sự là "1%" thì phải set thành số nguyên 1, không phải 1.0. Nên thống
    nhất 1 convention duy nhất trong settings về lâu dài thay vì dựa vào đoán.
    """
    threshold_percent = threshold * 100 if threshold <= 1.0 else threshold
    return max(0.0, min(100.0, threshold_percent))


async def evaluate_vision_result(
    keyword: str | None,
    confidence: float,
    raw_description: str,
) -> VisionAnalysis:
    """
    Xử lý Graceful Degradation:
    - Nếu confidence >= ngưỡng (mặc định 50%) và có keyword → OK
    - Nếu confidence thấp hoặc keyword rỗng → Gọi AWS Bedrock fallback từ raw_description
    - Nếu fallback cũng thất bại → Trả về AI_COULD_NOT_RECOGNIZE để client điều hướng nhập tay
    """
    threshold_percent = _normalize_threshold_percent(settings.VISION_CONFIDENCE_THRESHOLD)

    if keyword and confidence >= threshold_percent:
        return VisionAnalysis(
            status=DegradationResult.OK,
            keyword=keyword.lower().strip(),
            confidence=confidence,
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
