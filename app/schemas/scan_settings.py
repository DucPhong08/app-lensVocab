from pydantic import BaseModel, Field


class ScanSettings(BaseModel):
    max_detected_objects: int = Field(ge=1, le=10)
    max_allowed_objects: int = Field(ge=1, le=10)
    confidence_threshold: float = Field(ge=0, le=1)

    def accepts_confidence(self, percent: float) -> bool:
        return percent / 100 >= self.confidence_threshold
