from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from threading import Lock
from typing import Annotated

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

# Boto3 Client Config với adaptive retries (xử lý lỗi transient ở tầng AWS: throttle, timeout...)
_BOTO_CONFIG = Config(
    retries={"max_attempts": 3, "mode": "adaptive"},
    read_timeout=30,
    connect_timeout=10,
)


@lru_cache(maxsize=1)
def _get_boto3_session() -> boto3.Session:
    """Session chính: Rekognition, Polly — dùng AWS_REGION."""
    return boto3.Session(
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        region_name=settings.AWS_REGION,
    )


@lru_cache(maxsize=1)
def _get_bedrock_session() -> boto3.Session:
    """Keep the deployed Bedrock region; the legacy environment name is compatible."""
    bedrock_region = settings.AWS_EMBEDDING_REGION or settings.AWS_REGION
    return boto3.Session(
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        region_name=bedrock_region,
    )


# Create clients under a lock: a boto3 Session must not be used concurrently.
_client_lock = Lock()
_clients: dict[str, object] = {}


def _get_aws_client(service: str, *, bedrock: bool = False):
    key = "bedrock" if bedrock else service
    with _client_lock:
        if key not in _clients:
            session = _get_bedrock_session() if bedrock else _get_boto3_session()
            _clients[key] = session.client(service, config=_BOTO_CONFIG)
        return _clients[key]


def close_aws_clients() -> None:
    with _client_lock:
        for client in _clients.values():
            client.close()
        _clients.clear()


def _get_rekognition_client():
    return _get_aws_client("rekognition")


def _get_bedrock_client():
    # Keep the region configured in existing deployments.
    return _get_aws_client("bedrock-runtime", bedrock=True)


def _get_polly_client():
    return _get_aws_client("polly")


class BedrockOutputError(Exception):
    """Bedrock trả về nội dung không parse được thành JSON hợp lệ.

    Tách riêng khỏi ClientError vì lỗi này không phải lỗi AWS (không được
    _BOTO_CONFIG retry tự động) — tenacity chỉ retry loại lỗi này, tránh
    chồng retry với cơ chế adaptive retry sẵn có của boto3.
    """


# ─────────────────────────────────────────────────────────────────────────────
# 1. AWS Rekognition: Nhận diện đồ vật trong ảnh
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class BoundingBox:
    width: float
    height: float
    left: float
    top: float


@dataclass
class RekognitionLabel:
    name: str
    confidence: float
    categories: list[str]
    aliases: list[str]
    parents: list[str]
    bounding_box: BoundingBox | None = None
    has_instance: bool = False
    instances: list[BoundingBox] = field(default_factory=list)


@dataclass
class VisionResult:
    labels: list[RekognitionLabel]
    top_label: str | None
    top_confidence: float
    top_bounding_box: BoundingBox | None
    top_categories: list[str]
    top_aliases: list[str]
    top_parents: list[str]
    raw_description: str
    candidates: list[RekognitionLabel] = field(default_factory=list)


_CONTEXT_LABELS = frozenset(
    {
        "indoors",
        "outdoors",
        "interior design",
        "room",
        "nature",
        "wood",
        "plastic",
        "metal",
        "electronics",
        "furniture",
        "object",
        "material",
        "texture",
        "pattern",
    }
)


def _sync_detect_labels(
    image_bytes: bytes, max_labels: int = settings.REKOGNITION_MAX_LABELS
) -> VisionResult:
    client = _get_rekognition_client()
    safe_max = max(1, min(max_labels, 50))
    try:
        response = client.detect_labels(
            Image={"Bytes": image_bytes},
            MaxLabels=max(safe_max, settings.REKOGNITION_CANDIDATE_LIMIT),
            MinConfidence=settings.REKOGNITION_MIN_CONFIDENCE,
        )
    except ClientError as exc:
        logger.error("rekognition_failed: %s", exc)
        raise

    raw_labels = response.get("Labels", [])
    if not raw_labels:
        return VisionResult(
            labels=[],
            top_label=None,
            top_confidence=0.0,
            top_bounding_box=None,
            top_categories=[],
            top_aliases=[],
            top_parents=[],
            raw_description="",
        )

    parsed_labels: list[RekognitionLabel] = []
    for lbl in raw_labels:
        instances = lbl.get("Instances", [])
        boxes = []
        for instance in instances:
            raw_box = instance.get("BoundingBox", {})
            if instance.get("Confidence", lbl["Confidence"]) < settings.REKOGNITION_MIN_CONFIDENCE:
                continue
            candidate = BoundingBox(
                width=round(raw_box.get("Width", 0.0), 4),
                height=round(raw_box.get("Height", 0.0), 4),
                left=round(raw_box.get("Left", 0.0), 4),
                top=round(raw_box.get("Top", 0.0), 4),
            )
            if (
                0 < candidate.width <= 1
                and 0 < candidate.height <= 1
                and 0 <= candidate.left < 1
                and 0 <= candidate.top < 1
                and candidate.left + candidate.width <= 1.0001
                and candidate.top + candidate.height <= 1.0001
            ):
                boxes.append(candidate)
        has_instance = bool(boxes)
        box = boxes[0] if boxes else None

        parsed_labels.append(
            RekognitionLabel(
                name=lbl["Name"],
                confidence=lbl["Confidence"],
                categories=[c["Name"] for c in lbl.get("Categories", [])],
                aliases=[a["Name"] for a in lbl.get("Aliases", [])],
                parents=[p["Name"] for p in lbl.get("Parents", [])],
                bounding_box=box,
                has_instance=has_instance,
                instances=boxes,
            )
        )

    # Keep context for fallback, but prefer a learnable noun even without a box.
    parsed_labels.sort(
        key=lambda label: (
            label.name.casefold() not in _CONTEXT_LABELS,
            label.has_instance,
            label.confidence,
        ),
        reverse=True,
    )
    raw_desc = ", ".join(label.name for label in parsed_labels)
    candidates = parsed_labels
    parsed_labels = parsed_labels[:safe_max]
    top = parsed_labels[0]
    context_only = top.name.casefold() in _CONTEXT_LABELS

    logger.info(
        "rekognition_parsed top=%s (confidence=%.1f%%, has_box=%s, categories=%s, total=%d)",
        top.name,
        top.confidence,
        bool(top.bounding_box),
        top.categories,
        len(parsed_labels),
    )

    return VisionResult(
        labels=parsed_labels,
        top_label=None if context_only else top.name,
        top_confidence=top.confidence,
        top_bounding_box=None if context_only else top.bounding_box,
        top_categories=top.categories,
        top_aliases=top.aliases,
        top_parents=top.parents,
        raw_description=raw_desc,
        candidates=candidates,
    )


async def detect_image_labels(
    image_bytes: bytes, max_labels: int = settings.REKOGNITION_MAX_LABELS
) -> VisionResult:
    return await asyncio.to_thread(_sync_detect_labels, image_bytes, max_labels)


# ─────────────────────────────────────────────────────────────────────────────
# 2. AWS Bedrock (Amazon Nova): Sinh nội dung học tiếng Anh
# ─────────────────────────────────────────────────────────────────────────────


VocabText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class GeneratedFlashcard(BaseModel):
    model_config = ConfigDict(strict=True)
    keyword: VocabText
    pronunciation: VocabText
    meaning_vi: VocabText
    example_1: VocabText
    example_2: VocabText
    related_words: list[VocabText] = Field(min_length=1, max_length=10)


# Role/Goal/Output viết bằng tiếng Anh để giảm token input (tiếng Việt có dấu tốn
# nhiều token hơn ASCII qua BPE tokenizer). meaning_vi vẫn yêu cầu trả tiếng Việt
# vì đó là nội dung hiển thị cho người học — phần đó không đổi ngôn ngữ được.
_SYSTEM_PROMPT = (
    "Role: English vocabulary content generator for Vietnamese beginners (A1-A2 level).\n"
    "Goal: Produce accurate, level-appropriate flashcard content for one keyword.\n"
    "Output: Valid JSON only. No markdown, no code fences, no extra text."
)

_USER_PROMPT_TEMPLATE = """\
Context: Vietnamese learner rebuilding English from scratch ("mất gốc"), needs simple A1-A2 vocabulary.
Word: "{keyword}"

Required fields:
- pronunciation: accurate IPA (e.g. "/tʃer/")
- meaning_vi: Vietnamese meaning, most common sense, max 8 words
- example_1: simple sentence, A1-A2 level, max 10 words
- example_2: second sentence, different context, max 10 words
- related_words: 3 related English words, same topic

Return this exact JSON shape:
{{
  "pronunciation": "/.../",
  "meaning_vi": "...",
  "example_1": "...",
  "example_2": "...",
  "related_words": ["word1", "word2", "word3"]
}}
"""


def _extract_json(text_output: str) -> dict:
    """Bóc JSON ra khỏi output, kể cả khi model bọc trong ```json ... ```.

    Dùng chung cho generate_content và fallback_keyword để tránh lặp logic.
    """
    text_output = text_output.strip()
    if text_output.startswith("```"):
        parts = text_output.split("```")
        if len(parts) < 2:
            raise BedrockOutputError(f"unterminated code fence: {text_output!r}")
        text_output = parts[1]
        if text_output.startswith("json"):
            text_output = text_output[4:].strip()
    try:
        data = json.loads(text_output)
        if not isinstance(data, dict):
            raise BedrockOutputError("Expected a JSON object from Bedrock")
        return data
    except json.JSONDecodeError as exc:
        raise BedrockOutputError(f"invalid JSON from Bedrock: {text_output!r}") from exc


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type(BedrockOutputError),
)
def _sync_generate_content(keyword: str) -> GeneratedFlashcard:
    client = _get_bedrock_client()

    prompt = _USER_PROMPT_TEMPLATE.format(keyword=keyword)
    response = client.converse(
        modelId=settings.BEDROCK_MODEL_ID,
        system=[{"text": _SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"temperature": 0.2, "maxTokens": 200},
    )

    text_output = response["output"]["message"]["content"][0]["text"]
    data = _extract_json(text_output)

    try:
        return GeneratedFlashcard.model_validate({**data, "keyword": keyword.lower().strip()})
    except ValidationError as exc:
        raise BedrockOutputError("Invalid flashcard content from Bedrock") from exc


async def generate_flashcard_content(keyword: str) -> GeneratedFlashcard:
    return await asyncio.to_thread(_sync_generate_content, keyword)


# ─────────────────────────────────────────────────────────────────────────────
# 3. AWS Polly: Tạo âm thanh dùng chung cho từ vựng
# ─────────────────────────────────────────────────────────────────────────────


_POLLY_UNSUPPORTED_ENGINE_ERROR = "EngineNotSupportedException"


def _sync_synthesize_speech(
    text: str,
    voice_id: str | None = None,
    engine: str | None = None,
) -> str:
    """Gọi AWS Polly chuyển từ vựng thành MP3 audio và encode base64."""
    client = _get_polly_client()
    selected_voice = voice_id or settings.POLLY_VOICE_ID

    try:
        response = client.synthesize_speech(
            Text=text,
            OutputFormat=settings.POLLY_OUTPUT_FORMAT,
            VoiceId=selected_voice,
            Engine=engine or settings.POLLY_ENGINE,
        )
    except ClientError as exc:
        # Chỉ fallback về standard khi đúng lỗi "voice này chưa hỗ trợ neural
        # engine" ở region hiện tại. Các lỗi khác (auth, throttle, network...)
        # phải raise nguyên bản — tránh gọi lại vô ích rồi che mất lỗi gốc.
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code != _POLLY_UNSUPPORTED_ENGINE_ERROR:
            logger.error("polly_synthesize_failed: %s", exc)
            raise
        logger.warning("polly_neural_unsupported_fallback_standard: %s", exc)
        response = client.synthesize_speech(
            Text=text,
            OutputFormat=settings.POLLY_OUTPUT_FORMAT,
            VoiceId=selected_voice,
            Engine="standard",
        )

    with response["AudioStream"] as stream:
        audio_stream = stream.read()
    return base64.b64encode(audio_stream).decode("utf-8")


async def synthesize_speech(
    text: str,
    voice_id: str | None = None,
    engine: str | None = None,
) -> str:
    return await asyncio.to_thread(_sync_synthesize_speech, text, voice_id, engine)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Graceful Degradation Fallback: Tìm từ vựng từ bối cảnh
# ─────────────────────────────────────────────────────────────────────────────

_FALLBACK_SYSTEM_PROMPT = (
    "Role: Vocabulary suggester for Vietnamese English beginners (A1-A2 level).\n"
    "Goal: Suggest exactly one suitable English word from a scene description, "
    "or null if nothing fits.\n"
    "Output: Valid JSON only. No markdown, no extra text."
)

_FALLBACK_PROMPT_TEMPLATE = """\
Context: Vietnamese learner rebuilding English from scratch, scanning objects in a photo.
Scene description: "{description}"

Return this exact JSON shape:
{{
  "keyword": "english_word_or_null",
  "reason": "ngắn gọn bằng tiếng Việt giải thích vì sao chọn từ này"
}}
"""


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type(BedrockOutputError),
)
def _sync_fallback_keyword(description: str) -> str | None:
    client = _get_bedrock_client()
    prompt = _FALLBACK_PROMPT_TEMPLATE.format(description=description)

    response = client.converse(
        modelId=settings.BEDROCK_MODEL_ID,
        system=[{"text": _FALLBACK_SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"temperature": 0.2, "maxTokens": 100},
    )

    text_output = response["output"]["message"]["content"][0]["text"]
    data = _extract_json(text_output)
    keyword = data.get("keyword")
    if keyword is None:
        return None
    if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 100:
        raise BedrockOutputError("Invalid fallback keyword")
    return None if keyword.strip().lower() == "null" else keyword.strip().lower()


async def fallback_keyword_from_context(description: str) -> str | None:
    return await asyncio.to_thread(_sync_fallback_keyword, description)
