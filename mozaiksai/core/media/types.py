from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IMAGE_MIME_TYPES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
})

AG2_IMAGE_INPUT_MIME_TYPES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
})

AUDIO_MIME_TYPES: frozenset[str] = frozenset({
    "audio/wav",
    "audio/mpeg",
    "audio/ogg",
    "audio/flac",
    "audio/aiff",
    "audio/aac",
})

VIDEO_MIME_TYPES: frozenset[str] = frozenset({
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "video/x-matroska",
    "video/mpeg",
    "video/x-flv",
    "video/x-ms-wmv",
    "video/3gpp",
})

DOCUMENT_MIME_TYPES: frozenset[str] = frozenset({
    "application/json",
    "application/msword",
    "application/pdf",
    "application/rtf",
    "application/sql",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/xml",
    "application/yaml",
    "message/rfc822",
    "text/calendar",
    "text/csv",
    "text/css",
    "text/html",
    "text/javascript",
    "text/markdown",
    "text/plain",
    "text/srt",
    "text/tsv",
    "text/typescript",
    "text/vcard",
    "text/xml",
    "text/x-java",
    "text/x-python",
})

CHAT_ATTACHMENT_MIME_TYPES: frozenset[str] = (
    IMAGE_MIME_TYPES | DOCUMENT_MIME_TYPES
)

AG2_INPUT_MIME_TYPES: frozenset[str] = (
    AG2_IMAGE_INPUT_MIME_TYPES | AUDIO_MIME_TYPES | VIDEO_MIME_TYPES | DOCUMENT_MIME_TYPES
)


class MediaKind(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"


class MediaSource(StrEnum):
    UPLOADED = "uploaded"
    GENERATED = "generated"
    REMOTE = "remote"
    LOCAL = "local"
    STOCK = "stock"


class MediaPromotionTarget(StrEnum):
    BRAND_ASSET = "brand_asset"
    APP_ASSET = "app_asset"
    PAGE_ASSET = "page_asset"
    CAMPAIGN_ASSET = "campaign_asset"
    ARTIFACT = "artifact"


MediaPromotionTargetValue = Literal[
    "brand_asset",
    "app_asset",
    "page_asset",
    "campaign_asset",
    "artifact",
]


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_media_type(media_type: str | None) -> str:
    return str(media_type or "").split(";", 1)[0].strip().lower()


def media_kind_from_mime(media_type: str | None) -> MediaKind | None:
    normalized = normalize_media_type(media_type)
    if normalized in IMAGE_MIME_TYPES:
        return MediaKind.IMAGE
    if normalized in AUDIO_MIME_TYPES:
        return MediaKind.AUDIO
    if normalized in VIDEO_MIME_TYPES:
        return MediaKind.VIDEO
    if normalized in DOCUMENT_MIME_TYPES:
        return MediaKind.DOCUMENT
    return None


def is_ag2_input_media_type(media_type: str | None) -> bool:
    return normalize_media_type(media_type) in AG2_INPUT_MIME_TYPES


def extension_for_media_type(media_type: str | None, default: str = "bin") -> str:
    normalized = normalize_media_type(media_type)
    return {
        "application/json": "json",
        "application/pdf": "pdf",
        "application/rtf": "rtf",
        "application/xml": "xml",
        "application/yaml": "yaml",
        "audio/aac": "aac",
        "audio/aiff": "aiff",
        "audio/flac": "flac",
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "audio/wav": "wav",
        "image/gif": "gif",
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/svg+xml": "svg",
        "image/webp": "webp",
        "text/csv": "csv",
        "text/html": "html",
        "text/markdown": "md",
        "text/plain": "txt",
        "text/tsv": "tsv",
        "video/mp4": "mp4",
        "video/quicktime": "mov",
        "video/webm": "webm",
        "video/x-matroska": "mkv",
    }.get(normalized, default)


class MediaInputRef(BaseModel):
    """Provider-neutral reference to media a workflow can pass to an agent."""

    model_config = ConfigDict(extra="forbid")

    kind: MediaKind
    media_type: str
    source: MediaSource = MediaSource.UPLOADED
    filename: str | None = None
    url: str | None = None
    path: str | None = None
    file_id: str | None = None
    content_ref: str | None = None
    attachment_id: str | None = None
    data: bytes | None = Field(default=None, repr=False, exclude=True)
    metadata: dict[str, Any] = Field(default_factory=dict)
    vendor_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_reference(self) -> MediaInputRef:
        normalized_type = normalize_media_type(self.media_type)
        if not normalized_type:
            raise ValueError("media_type is required")
        inferred_kind = media_kind_from_mime(normalized_type)
        if inferred_kind is not None and inferred_kind != self.kind:
            raise ValueError(f"media_type {normalized_type!r} does not match kind {self.kind!s}")
        has_ref = any([
            bool(self.url),
            bool(self.path),
            bool(self.file_id),
            bool(self.content_ref),
            bool(self.attachment_id),
            self.data is not None,
        ])
        if not has_ref:
            raise ValueError("one of url, path, file_id, content_ref, attachment_id, or data is required")
        self.media_type = normalized_type
        return self


class GeneratedMediaAsset(BaseModel):
    """Durable metadata for AI-generated media bytes."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="_id")
    asset_id: str
    app_id: str
    kind: MediaKind
    media_type: str
    source: MediaSource = MediaSource.GENERATED
    filename: str
    size_bytes: int = Field(ge=0)
    sha256: str
    content_ref: str
    content_backend: str
    source_workflow: str | None = None
    source_chat_id: str | None = None
    prompt: str | None = None
    provider: str | None = None
    model: str | None = None
    generation_params: dict[str, Any] = Field(default_factory=dict)
    promotion_targets: list[MediaPromotionTarget] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    def to_asset_manifest_entry(
        self,
        *,
        path: str | None = None,
        url: str | None = None,
        usage: list[str] | None = None,
        tags: list[str] | None = None,
        alt: str | None = None,
        license: str | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "asset_id": self.asset_id,
            "kind": self.kind.value,
            "source": self.source.value,
            "alt": alt,
            "license": license,
            "usage": list(usage or []),
            "tags": list(tags or []),
        }
        if path:
            entry["path"] = path
        if url:
            entry["url"] = url
        if not path and not url:
            entry["path"] = self.filename
        return {key: value for key, value in entry.items() if value is not None}


class MediaArtifactPayload(BaseModel):
    """UI/tool payload shape for generated media proposals."""

    model_config = ConfigDict(extra="forbid")

    artifact_type: str = "core.media.generated_asset"
    asset: GeneratedMediaAsset
    preview_url: str | None = None
    promotion_targets: list[MediaPromotionTarget] = Field(default_factory=list)
