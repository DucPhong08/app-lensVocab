from __future__ import annotations

import asyncio
import io
import logging
import math
import re
import uuid
import warnings
from threading import BoundedSemaphore, Lock

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings
from app.models.flashcard import UserFlashcard
from app.schemas.flashcard import CardImageResponse, CropBox
from app.services.sticker_service import StickerError, sticker

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
_client = None
_client_lock = Lock()
_sticker_gate = BoundedSemaphore(1)


class CardImageError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.status = status


def _s3_client():
    global _client
    if not settings.S3_IMAGE_BUCKET:
        raise CardImageError("IMAGE_STORAGE_NOT_CONFIGURED", 503)
    endpoint = settings.S3_IMAGE_ENDPOINT_URL
    if endpoint and not (settings.S3_IMAGE_ACCESS_KEY_ID and settings.S3_IMAGE_SECRET_ACCESS_KEY):
        raise CardImageError("IMAGE_STORAGE_NOT_CONFIGURED", 503)
    with _client_lock:
        if _client is None:
            _client = boto3.client(
                "s3",
                endpoint_url=endpoint or None,
                aws_access_key_id=(
                    settings.S3_IMAGE_ACCESS_KEY_ID if endpoint else settings.AWS_ACCESS_KEY_ID
                )
                or None,
                aws_secret_access_key=(
                    settings.S3_IMAGE_SECRET_ACCESS_KEY
                    if endpoint
                    else settings.AWS_SECRET_ACCESS_KEY
                )
                or None,
                region_name=(
                    endpoint.split(".")[1]
                    if endpoint
                    else settings.S3_IMAGE_REGION or settings.AWS_REGION
                ),
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=3,
                    read_timeout=10,
                    retries={"max_attempts": 2},
                    s3={"addressing_style": "path" if endpoint else "virtual"},
                    # Avoid optional AWS checksum trailers unsupported by B2.
                    request_checksum_calculation="when_required" if endpoint else "when_supported",
                    response_checksum_validation="when_required" if endpoint else "when_supported",
                ),
            )
    return _client


def close_storage_client() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


def owned_image_key(card: UserFlashcard) -> bool:
    prefix = f"user-images/{card.user_id.hex}/{card.id.hex}/"
    return bool(
        card.image_key
        and re.fullmatch(re.escape(prefix) + r"[0-9a-f]{32}\.(jpg|png)", card.image_key)
    )


def prepare_image(data: bytes, crop: CropBox | None = None, *, style: str = "photo") -> bytes:
    if style != "sticker":
        return _prepare_image(data, crop, style=style)
    # Bound the entire decode/inference/encode job, including cancelled requests.
    if not _sticker_gate.acquire(blocking=False):
        raise CardImageError("STICKER_BUSY", 429)
    try:
        return _prepare_image(data, crop, style=style)
    finally:
        _sticker_gate.release()


def _prepare_image(data: bytes, crop: CropBox | None = None, *, style: str = "photo") -> bytes:
    if not data:
        raise CardImageError("FILE_EMPTY")
    if len(data) > MAX_IMAGE_BYTES:
        raise CardImageError("IMAGE_TOO_LARGE", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=("JPEG", "PNG")) as source:
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise CardImageError("IMAGE_PIXEL_LIMIT_EXCEEDED", 413)
                source.verify()
            with Image.open(io.BytesIO(data), formats=("JPEG", "PNG")) as source:
                source.load()
                # Rekognition corrects JPEG EXIF coordinates, but leaves PNG coordinates raw.
                image = ImageOps.exif_transpose(source) if source.format == "JPEG" else source
                if crop is not None:
                    width, height = image.size
                    left, top = math.floor(crop.left * width), math.floor(crop.top * height)
                    right = min(width, max(left + 1, math.ceil((crop.left + crop.width) * width)))
                    bottom = min(height, max(top + 1, math.ceil((crop.top + crop.height) * height)))
                    image = image.crop((left, top, right, bottom))
                if source.format == "PNG":
                    image = ImageOps.exif_transpose(image)
                if style == "sticker":
                    try:
                        result = sticker(image)
                    except StickerError as exc:
                        raise CardImageError(str(exc), 503 if "MODEL" in str(exc) else 422) from exc
                    output = io.BytesIO()
                    result.save(output, format="PNG", optimize=True)
                    return output.getvalue()
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                # A new pixel canvas carries no EXIF/GPS, XMP, comments or ICC data.
                clean = Image.new("RGB", image.size, "white")
                if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    clean.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    clean.paste(image.convert("RGB"))
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=85, optimize=True)
                return output.getvalue()
    except CardImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise CardImageError("IMAGE_PIXEL_LIMIT_EXCEEDED", 413) from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise CardImageError("INVALID_IMAGE_DATA") from exc


def _signed_url(card: UserFlashcard) -> str:
    if not owned_image_key(card):
        raise CardImageError("IMAGE_NOT_AVAILABLE", 404)
    try:
        return _s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_IMAGE_BUCKET, "Key": card.image_key},
            ExpiresIn=settings.S3_IMAGE_URL_TTL_SECONDS,
        )
    except (BotoCoreError, ClientError) as exc:
        raise CardImageError("IMAGE_STORAGE_UNAVAILABLE", 503) from exc


async def card_image_urls(cards: list[UserFlashcard]) -> dict[uuid.UUID, str]:
    if not settings.S3_IMAGE_BUCKET or not any(card.image_key for card in cards):
        return {}

    def sign():
        urls = {}
        for card in cards:
            if not owned_image_key(card):
                continue
            try:
                urls[card.id] = _signed_url(card)
            except CardImageError:
                logger.warning("card_image_sign_failed")
        return urls

    return await asyncio.to_thread(sign)


async def _owned_card(user_id: uuid.UUID, card_id: uuid.UUID) -> UserFlashcard:
    card = await UserFlashcard.find_one({"_id": card_id, "user_id": user_id})
    if card is None:
        raise CardImageError("FLASHCARD_NOT_FOUND", 404)
    return card


async def get_card_image(user_id: uuid.UUID, card_id: uuid.UUID) -> CardImageResponse:
    card = await _owned_card(user_id, card_id)
    return CardImageResponse(
        image_url=await asyncio.to_thread(_signed_url, card),
        has_image=True,
        image_style="sticker" if card.image_key.endswith(".png") else "photo",
    )


def _put_object(key: str, data: bytes) -> None:
    try:
        _s3_client().put_object(
            Bucket=settings.S3_IMAGE_BUCKET,
            Key=key,
            Body=data,
            ContentType="image/png" if key.endswith(".png") else "image/jpeg",
            CacheControl="private, max-age=300",
            ServerSideEncryption="AES256",
        )
    except (BotoCoreError, ClientError) as exc:
        raise CardImageError("IMAGE_STORAGE_UNAVAILABLE", 503) from exc


def _delete_object(card: UserFlashcard) -> None:
    if not owned_image_key(card):
        return
    try:
        client = _s3_client()
        if not settings.S3_IMAGE_ENDPOINT_URL:
            client.delete_object(Bucket=settings.S3_IMAGE_BUCKET, Key=card.image_key)
            return
        # B2 retains versions: DELETE without VersionId only adds a hide marker.
        pages = client.get_paginator("list_object_versions").paginate(
            Bucket=settings.S3_IMAGE_BUCKET, Prefix=card.image_key
        )
        versions = [
            item
            for page in pages
            for field in ("Versions", "DeleteMarkers")
            for item in page.get(field, [])
            if item["Key"] == card.image_key
        ]
        for item in versions:
            client.delete_object(
                Bucket=settings.S3_IMAGE_BUCKET, Key=card.image_key, VersionId=item["VersionId"]
            )
    except (BotoCoreError, ClientError) as exc:
        raise CardImageError("IMAGE_STORAGE_UNAVAILABLE", 503) from exc


async def _cleanup(card: UserFlashcard) -> None:
    try:
        await asyncio.to_thread(_delete_object, card)
    except CardImageError:
        logger.warning("card_image_cleanup_failed")


async def set_card_image(
    user_id: uuid.UUID,
    card_id: uuid.UUID,
    data: bytes,
    crop: CropBox | None = None,
    *,
    style: str = "photo",
) -> CardImageResponse:
    card = await _owned_card(user_id, card_id)
    if not settings.S3_IMAGE_BUCKET:
        raise CardImageError("IMAGE_STORAGE_NOT_CONFIGURED", 503)
    content = await asyncio.to_thread(prepare_image, data, crop, style=style)
    extension = "png" if style == "sticker" else "jpg"
    key = f"user-images/{user_id.hex}/{card_id.hex}/{uuid.uuid4().hex}.{extension}"
    updated = card.model_copy(update={"image_key": key})
    try:
        await asyncio.to_thread(_put_object, key, content)
        result = await UserFlashcard.find_one(
            {"_id": card_id, "user_id": user_id, "image_key": card.image_key}
        ).update({"$set": {"image_key": key}})
        if result.matched_count != 1:
            raise CardImageError("IMAGE_UPDATE_CONFLICT", 409)
    except Exception:
        await _cleanup(updated)
        raise
    await _cleanup(card)
    urls = await card_image_urls([updated])
    return CardImageResponse(image_url=urls.get(updated.id), has_image=True, image_style=style)


async def delete_card_image(user_id: uuid.UUID, card_id: uuid.UUID) -> CardImageResponse:
    card = await _owned_card(user_id, card_id)
    if not card.image_key:
        return CardImageResponse()
    await asyncio.to_thread(_delete_object, card)
    result = await UserFlashcard.find_one(
        {"_id": card_id, "user_id": user_id, "image_key": card.image_key}
    ).update({"$set": {"image_key": None}})
    if result.matched_count != 1:
        raise CardImageError("IMAGE_UPDATE_CONFLICT", 409)
    return CardImageResponse()
