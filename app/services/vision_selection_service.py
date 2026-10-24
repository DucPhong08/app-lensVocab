from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from pydantic import ValidationError

from app.config import settings
from app.models.user import User
from app.schemas.flashcard import CropBox
from app.schemas.vision import BoundingBoxSchema, DetectedObjectItem, ScanResponse
from app.services.ai_service import VisionResult
from app.services.cache_service import get_flashcard
from app.services.system_setting_service import fetch_system_settings


class ObjectSelectionError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.status = status


def overlap(a: BoundingBoxSchema, b: BoundingBoxSchema) -> float:
    width = max(0, min(a.left + a.width, b.left + b.width) - max(a.left, b.left))
    height = max(0, min(a.top + a.height, b.top + b.height) - max(a.top, b.top))
    intersection = width * height
    union = a.width * a.height + b.width * b.height - intersection
    return intersection / union if union else 0


def detected_objects(result: VisionResult, limit: int, user: User | None):
    scan_id = uuid.uuid4().hex
    items: list[DetectedObjectItem] = []
    entries = [
        (label, label.instances or ([label.bounding_box] if label.bounding_box else [None]))
        for label in (result.candidates or result.labels)
    ]
    # Show distinct labels before additional instances of the same label.
    for index in range(max((len(boxes) for _, boxes in entries), default=0)):
        for label, boxes in entries:
            if index >= len(boxes):
                continue
            box = boxes[index]
            bounds = None
            if box is not None:
                try:
                    crop = CropBox(width=box.width, height=box.height, left=box.left, top=box.top)
                    bounds = BoundingBoxSchema(**crop.model_dump())
                except ValidationError:
                    continue
                if any(
                    item.bounding_box and overlap(bounds, item.bounding_box) >= 0.85
                    for item in items
                ):
                    continue
            item = DetectedObjectItem(
                object_id=uuid.uuid4().hex,
                keyword=label.name,
                confidence=round(label.confidence, 2),
                bounding_box=bounds,
                categories=label.categories,
                aliases=label.aliases,
                parents=label.parents,
            )
            if (
                bounds is not None
                and label.confidence >= settings.VISION_CONFIDENCE_THRESHOLD * 100
            ):
                # No sub claim: this token must never authenticate an application session.
                item.selection_token = jwt.encode(
                    {
                        "kind": "vision_object",
                        "owner": str(user.id) if user else None,
                        "scan_id": scan_id,
                        "object": item.model_dump(exclude={"selection_token"}),
                        "exp": datetime.now(UTC) + timedelta(minutes=30),
                    },
                    settings.SECRET_KEY,
                    algorithm=settings.ALGORITHM,
                )
            items.append(item)
            if len(items) >= limit:
                return items
    return items


async def select_object(token: str, user: User | None, redis) -> ScanResponse:
    try:
        data = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if data.get("kind") != "vision_object" or not data.get("exp"):
            raise ValueError("wrong token kind")
        if data.get("owner") is not None and data["owner"] != (str(user.id) if user else None):
            raise ValueError("wrong owner")
        item = DetectedObjectItem.model_validate(data["object"])
        if item.bounding_box is None:
            raise ValueError("missing region")
        CropBox.model_validate(item.bounding_box.model_dump())
    except (JWTError, ValueError, KeyError, TypeError, ValidationError) as exc:
        raise ObjectSelectionError("OBJECT_SELECTION_EXPIRED_OR_INVALID", 400) from exc
    system = await fetch_system_settings()
    if system.maintenance_mode:
        raise ObjectSelectionError("MAINTENANCE_MODE", 503)
    # Vocabulary is generated only for the chosen, server-signed object.
    payload = await get_flashcard(item.keyword, redis)
    return ScanResponse(
        status="OK",
        selected_object_id=item.object_id,
        keyword=payload.keyword,
        pronunciation=payload.pronunciation,
        meaning_vi=payload.meaning_vi,
        example_1=payload.example_1,
        example_2=payload.example_2,
        related_words=payload.related_words,
        audio_base64=payload.audio_base64,
        aliases=item.aliases,
        parents=item.parents,
        categories=item.categories,
        bounding_box=item.bounding_box,
        confidence=item.confidence,
        source=payload.source,
        is_draft=payload.is_draft,
    )
