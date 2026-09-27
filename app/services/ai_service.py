from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
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
    """Tạo AWS Session singleton từ IAM Credentials."""
    return boto3.Session(
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        region_name=settings.AWS_REGION,
    )


def _get_rekognition_client():
    return _get_boto3_session().client("rekognition", config=_BOTO_CONFIG)


def _get_bedrock_client():
    return _get_boto3_session().client("bedrock-runtime", config=_BOTO_CONFIG)


def _get_polly_client():
    return _get_boto3_session().client("polly", config=_BOTO_CONFIG)


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


def _sync_detect_labels(image_bytes: bytes) -> VisionResult:
    client = _get_rekognition_client()
    try:
        response = client.detect_labels(
            Image={"Bytes": image_bytes},
            MaxLabels=settings.REKOGNITION_MAX_LABELS,
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
        has_instance = len(instances) > 0
        box: BoundingBox | None = None
        if has_instance:
            raw_box = instances[0].get("BoundingBox", {})
            box = BoundingBox(
                width=round(raw_box.get("Width", 0.0), 4),
                height=round(raw_box.get("Height", 0.0), 4),
                left=round(raw_box.get("Left", 0.0), 4),
                top=round(raw_box.get("Top", 0.0), 4),
            )

        parsed_labels.append(
            RekognitionLabel(
                name=lbl["Name"],
                confidence=lbl["Confidence"],
                categories=[c["Name"] for c in lbl.get("Categories", [])],
                aliases=[a["Name"] for a in lbl.get("Aliases", [])],
                parents=[p["Name"] for p in lbl.get("Parents", [])],
                bounding_box=box,
                has_instance=has_instance,
            )
        )

    # ── Thuật toán chọn nhãn thông minh (Smart Selection) ────────────────────
    # 1. Ưu tiên đồ vật cụ thể có tọa độ (Instances > 0) trước để người học
    #    học từ thực thể (Chair, Bottle) thay vì chất liệu/bối cảnh (Wood, Indoors).
    # 2. Trong cùng nhóm, sắp xếp theo confidence giảm dần.
    parsed_labels.sort(key=lambda x: (x.has_instance, x.confidence), reverse=True)
    top = parsed_labels[0]
    raw_desc = ", ".join(lbl.name for lbl in parsed_labels)

    logger.info(
        "rekognition_parsed top=%s (confidence=%.1f%%, has_box=%s, categories=%s)",
        top.name,
        top.confidence,
        bool(top.bounding_box),
        top.categories,
    )

    return VisionResult(
        labels=parsed_labels,
        top_label=top.name,
        top_confidence=top.confidence,
        top_bounding_box=top.bounding_box,
        top_categories=top.categories,
        top_aliases=top.aliases,
        top_parents=top.parents,
        raw_description=raw_desc,
    )


async def detect_image_labels(image_bytes: bytes) -> VisionResult:
    return await asyncio.to_thread(_sync_detect_labels, image_bytes)


# ─────────────────────────────────────────────────────────────────────────────
# 2. AWS Bedrock (Amazon Nova): Sinh nội dung học tiếng Anh
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GeneratedFlashcard:
    keyword: str
    pronunciation: str           # /tʃer/
    meaning_vi: str              # Nghĩa tiếng Việt
    example_1: str               # Câu ví dụ 1
    example_2: str               # Câu ví dụ 2
    related_words: list[str]     # ["seat", "sofa", "stool"]


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
        return json.loads(text_output)
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
        inferenceConfig={"temperature": 0.2, "maxTokens": 300},
    )

    text_output = response["output"]["message"]["content"][0]["text"]
    data = _extract_json(text_output)

    return GeneratedFlashcard(
        keyword=keyword.lower().strip(),
        pronunciation=data.get("pronunciation", ""),
        meaning_vi=data.get("meaning_vi", ""),
        example_1=data.get("example_1", ""),
        example_2=data.get("example_2", ""),
        related_words=data.get("related_words", []),
    )


async def generate_flashcard_content(keyword: str) -> GeneratedFlashcard:
    # TODO: check cache/DB theo `keyword` trước khi gọi Bedrock ở đây — nội dung
    # flashcard gần như bất biến theo từ, nên gọi lại Bedrock cho từ đã sinh
    # trước đó là chi phí tránh được lớn nhất trong luồng này (lớn hơn nhiều so
    # với việc rút gọn token của prompt).
    return await asyncio.to_thread(_sync_generate_content, keyword)


# ─────────────────────────────────────────────────────────────────────────────
# 3. AWS Bedrock (Amazon Titan Embeddings v2): Tạo vector ngữ nghĩa
# ─────────────────────────────────────────────────────────────────────────────

@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type(KeyError),
)
def _sync_create_embedding(text: str) -> list[float]:
    client = _get_bedrock_client()
    body = json.dumps({"inputText": text.lower().strip()})

    response = client.invoke_model(
        modelId=settings.BEDROCK_EMBEDDING_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=body,
    )

    data = json.loads(response["body"].read())
    try:
        return data["embedding"]
    except KeyError:
        logger.error("titan_embedding_missing_field: %s", data)
        raise


async def create_titan_embedding(text: str) -> list[float]:
    return await asyncio.to_thread(_sync_create_embedding, text)


# ─────────────────────────────────────────────────────────────────────────────
# 4. AWS Polly: Phát âm từ vựng (Text-To-Speech)
# ─────────────────────────────────────────────────────────────────────────────

_POLLY_UNSUPPORTED_ENGINE_ERROR = "EngineNotSupportedException"


def _sync_synthesize_speech(text: str) -> str:
    """Gọi AWS Polly chuyển từ vựng thành MP3 audio và encode base64."""
    client = _get_polly_client()

    try:
        response = client.synthesize_speech(
            Text=text,
            OutputFormat=settings.POLLY_OUTPUT_FORMAT,
            VoiceId=settings.POLLY_VOICE_ID,
            Engine="neural",  # Giọng đọc AI tự nhiên chất lượng cao
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
            VoiceId=settings.POLLY_VOICE_ID,
            Engine="standard",
        )

    audio_stream = response["AudioStream"].read()
    return base64.b64encode(audio_stream).decode("utf-8")


async def synthesize_speech(text: str) -> str:
    return await asyncio.to_thread(_sync_synthesize_speech, text)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Graceful Degradation Fallback: Tìm từ vựng từ bối cảnh
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
    return data.get("keyword")


async def fallback_keyword_from_context(description: str) -> str | None:
    return await asyncio.to_thread(_sync_fallback_keyword, description)