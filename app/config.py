from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ─────────────────────────────────────────────────────────────
    MONGODB_URL: str = Field("mongodb://root:root@localhost:27017/?authSource=admin", repr=False)
    MONGODB_DB_NAME: str = "lensvocab"

    # ── Cache ────────────────────────────────────────────────────────────────
    REDIS_URL: str = Field("redis://localhost:6379/0", repr=False)
    REDIS_CACHE_TTL: int = 86400 * 7  # 7 ngày
    REDIS_QUOTA_TTL: int = 86400  # 24 giờ

    # ── Security ─────────────────────────────────────────────────────────────
    # Bắt buộc, không có default — thiếu env var này sẽ fail ngay lúc khởi
    # động thay vì chạy ngầm với key insecure đã biết trước trong source.
    SECRET_KEY: str = Field(repr=False)
    GOOGLE_WEB_CLIENT_ID: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # ── AWS Infrastructure (IAM API Keys: Rekognition, Bedrock, Polly) ───────
    AWS_ACCESS_KEY_ID: str = Field("", repr=False)
    AWS_SECRET_ACCESS_KEY: str = Field("", repr=False)
    AWS_REGION: str = "ap-southeast-1"  # Region chính: Rekognition, Polly
    # Region Bedrock cho Nova Lite; giữ tên env legacy để không đổi deployment
    AWS_EMBEDDING_REGION: str = ""  # Nếu để trống → fallback về AWS_REGION

    # AWS Bedrock
    BEDROCK_MODEL_ID: str = "amazon.nova-lite-v1:0"
    BEDROCK_EMBEDDING_MODEL_ID: str = "amazon.titan-embed-text-v2:0"

    # AWS Rekognition
    REKOGNITION_MAX_LABELS: int = 5
    REKOGNITION_MIN_CONFIDENCE: float = Field(25.0, ge=0, le=100)
    REKOGNITION_CANDIDATE_LIMIT: int = Field(30, ge=20, le=1000)

    # AWS Polly
    POLLY_VOICE_ID: str = "Joanna"
    POLLY_OUTPUT_FORMAT: str = "mp3"
    POLLY_ENGINE: Literal["standard", "neural"] = "standard"

    IMAGE_SEGMENTATION_MODEL_PATH: str = "models/u2netp.onnx"

    # Private object storage; an empty bucket disables image uploads.
    S3_IMAGE_BUCKET: str = ""
    S3_IMAGE_REGION: str = ""
    # Optional Backblaze endpoint; empty keeps AWS S3 and its existing credentials.
    S3_IMAGE_ENDPOINT_URL: str = Field("", pattern=r"^(https://s3\.[a-z0-9-]+\.backblazeb2\.com)?$")
    S3_IMAGE_ACCESS_KEY_ID: str = Field("", repr=False)
    S3_IMAGE_SECRET_ACCESS_KEY: str = Field("", repr=False)
    S3_IMAGE_URL_TTL_SECONDS: int = Field(3600, ge=60, le=86400)

    # ── Business Rules ───────────────────────────────────────────────────────
    FREE_DAILY_QUOTA: int = 10
    GUEST_DAILY_QUOTA: int = 3
    GUEST_GLOBAL_DAILY_QUOTA: int = 100
    GUEST_SCAN_INTERVAL_SECONDS: int = 10
    REVIEW_DAILY_CAP: int = 15
    VISION_CONFIDENCE_THRESHOLD: float = 0.50
    SEMANTIC_SIMILARITY_THRESHOLD: float = 0.85
    VECTOR_INDEX_NAME: str = "vocab_embedding_index"

    # ── Proxy & Network ──────────────────────────────────────────────────────
    TRUST_PROXY_HEADERS: bool = False
    TRUSTED_PROXIES: str = "127.0.0.1,::1"
    PROXY_CLIENT_IP_HEADER: Literal["x-forwarded-for", "x-real-ip", "cf-connecting-ip"] = (
        "x-forwarded-for"
    )


settings = Settings()
