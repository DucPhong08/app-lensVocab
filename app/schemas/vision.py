from __future__ import annotations

from pydantic import BaseModel


class BoundingBoxSchema(BaseModel):
    width: float  # Chiều rộng khung (tỉ lệ 0.0 - 1.0)
    height: float  # Chiều cao khung (tỉ lệ 0.0 - 1.0)
    left: float  # Tọa độ X góc trên bên trái (tỉ lệ 0.0 - 1.0)
    top: float  # Tọa độ Y góc trên bên trái (tỉ lệ 0.0 - 1.0)


class DetectedObjectItem(BaseModel):
    keyword: str  # Tên nhãn tiếng Anh (ví dụ "chair", "laptop")
    confidence: float  # Độ tin cậy (0 - 100)
    bounding_box: BoundingBoxSchema | None = None  # Khung bao quanh vật thể (Google Lens style)
    categories: list[str] = []
    aliases: list[str] = []
    parents: list[str] = []


class ScanResponse(BaseModel):
    status: str  # "OK" | "FALLBACK" | "AI_COULD_NOT_RECOGNIZE"
    keyword: str | None = None  # Từ tiếng Anh: "chair"
    pronunciation: str | None = None  # Phiên âm IPA: "/tʃer/"
    meaning_vi: str | None = None  # Nghĩa tiếng Việt: "cái ghế"
    example_1: str | None = None  # Ví dụ 1: "I sit on a chair"
    example_2: str | None = None  # Ví dụ 2: "This wooden chair is sturdy"
    related_words: list[str] = []  # Từ liên quan: ["seat", "sofa", "stool"]
    aliases: list[str] = []  # Tên gọi khác từ Rekognition: ["Seat"]
    categories: list[str] = []  # Danh mục: ["Furniture"]
    parents: list[str] = []  # Danh mục cha: ["Furniture", "Home Decor"]
    bounding_box: BoundingBoxSchema | None = (
        None  # Tọa độ để Frontend vẽ khung viền (Google Lens style)
    )
    detected_objects: list[DetectedObjectItem] = []  # Toàn bộ danh sách vật thể phát hiện được
    audio_base64: str | None = None  # MP3 audio từ AWS Polly
    confidence: float = 0.0
    source: str | None = None  # "redis" | "mongodb" | "bedrock"
    is_draft: bool = False
    message: str | None = None
