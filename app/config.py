from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ─────────────────────────────────────────────────────────────
    MONGODB_URL: str = "mongodb://root:root@localhost:27017/?authSource=admin"
    MONGODB_DB_NAME: str = "lensvocab"

    # ── Cache ────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CACHE_TTL: int = 86400 * 7  # 7 ngày
    REDIS_QUOTA_TTL: int = 86400  # 24 giờ

    # ── Security ─────────────────────────────────────────────────────────────
    # Bắt buộc, không có default — thiếu env var này sẽ fail ngay lúc khởi
    # động thay vì chạy ngầm với key insecure đã biết trước trong source.
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # ── AWS Infrastructure (IAM API Keys: Rekognition, Bedrock, Polly) ───────
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "ap-southeast-1"  # Region chính: Rekognition, Nova Lite, Polly
    # Titan Embeddings v2 chưa có ở ap-southeast-1 → dùng region riêng nếu đổi sang SIN
    AWS_EMBEDDING_REGION: str = ""  # Nếu để trống → fallback về AWS_REGION

    # AWS Bedrock
    BEDROCK_MODEL_ID: str = "amazon.nova-lite-v1:0"
    BEDROCK_EMBEDDING_MODEL_ID: str = "amazon.titan-embed-text-v2:0"

    # AWS Rekognition
    REKOGNITION_MAX_LABELS: int = 5
    REKOGNITION_MIN_CONFIDENCE: float = 50.0

    # AWS Polly
    POLLY_VOICE_ID: str = "Joanna"
    POLLY_OUTPUT_FORMAT: str = "mp3"

    # ── Business Rules ───────────────────────────────────────────────────────
    FREE_DAILY_QUOTA: int = 10
    GUEST_DAILY_QUOTA: int = 3
    GUEST_GLOBAL_DAILY_QUOTA: int = 100
    GUEST_SCAN_INTERVAL_SECONDS: int = 10
    REVIEW_DAILY_CAP: int = 15
    VISION_CONFIDENCE_THRESHOLD: float = 0.50
    SEMANTIC_SIMILARITY_THRESHOLD: float = 0.85
    VECTOR_INDEX_NAME: str = "vocab_embedding_index"


settings = Settings()
