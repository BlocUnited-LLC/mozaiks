"""Bounded decoding and normalization for profile images."""
import warnings
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_STORED_BYTES = 2 * 1024 * 1024


def normalize_profile_image(data: bytes, kind: str) -> bytes:
    if kind not in {"avatar", "cover"}:
        raise ValueError("Choose avatar or cover")
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Choose an image smaller than 5 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Choose a JPEG, PNG, or WebP image")
                if image.width * image.height > 16_000_000:
                    raise ValueError("Choose an image with at most 16 million pixels")
                image.load()
                normalized = ImageOps.exif_transpose(image).convert("RGB")
                normalized.thumbnail((1024, 1024) if kind == "avatar" else (2048, 1024))
                output = BytesIO()
                normalized.save(output, format="JPEG", quality=85)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Choose a valid JPEG, PNG, or WebP image") from exc
    result = output.getvalue()
    if len(result) > MAX_STORED_BYTES:
        raise ValueError("Choose a smaller image")
    return result
