from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from logs.logging_config import get_core_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id, dual_write_app_scope

from .types import (
    GeneratedMediaAsset,
    MediaKind,
    MediaPromotionTarget,
    extension_for_media_type,
    media_kind_from_mime,
    normalize_media_type,
)

logger = get_core_logger("media_store")


class MediaContentNotFoundError(Exception):
    """Raised when a media content reference cannot be resolved."""


@runtime_checkable
class MediaContentStore(Protocol):
    """Pluggable byte store for generated media assets."""

    backend_name: str

    async def put_media(
        self,
        data: bytes,
        *,
        app_id: str,
        media_id: str,
        filename: str,
        media_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store media bytes and return an opaque content reference."""
        ...

    async def get_media(self, content_ref: str) -> bytes:
        """Retrieve media bytes by opaque content reference."""
        ...

    async def exists(self, content_ref: str) -> bool:
        """Return True when the content reference resolves to media bytes."""
        ...

    async def delete(self, content_ref: str) -> bool:
        """Delete media bytes if present."""
        ...


def _safe_filename(filename: str | None, *, media_type: str, fallback_stem: str) -> str:
    raw = Path(str(filename or "")).name.strip()
    if not raw:
        raw = f"{fallback_stem}.{extension_for_media_type(media_type)}"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-")
    return cleaned or f"{fallback_stem}.{extension_for_media_type(media_type)}"


def _media_root_from_env() -> Path:
    return Path(os.getenv("MOZAIKS_MEDIA_STORAGE_DIR", str((Path.cwd() / "generated_media").resolve()))).resolve()


def _media_type_from_metadata(metadata: dict[str, Any]) -> str:
    direct = (
        metadata.get("media_type")
        or metadata.get("mime_type")
        or metadata.get("mimeType")
        or metadata.get("mediaType")
        or metadata.get("content_type")
        or metadata.get("contentType")
    )
    normalized = normalize_media_type(str(direct or ""))
    if normalized:
        return normalized
    output_format = str(metadata.get("output_format") or metadata.get("format") or "").strip().lower().lstrip(".")
    if output_format in {"jpg", "jpeg"}:
        return "image/jpeg"
    if output_format in {"png", "webp", "gif"}:
        return f"image/{output_format}"
    return "image/png"


class LocalMediaContentStore:
    """Stores generated media bytes on the local filesystem."""

    backend_name = "local"

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root).resolve() if root else _media_root_from_env()

    def _resolve_content_ref(self, content_ref: str) -> Path:
        path = Path(str(content_ref)).resolve()
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise MediaContentNotFoundError("Local media content reference is outside the media storage root") from exc
        return path

    async def put_media(
        self,
        data: bytes,
        *,
        app_id: str,
        media_id: str,
        filename: str,
        media_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        _ = metadata
        safe_name = _safe_filename(filename, media_type=media_type, fallback_stem=media_id)
        dest_dir = (self._root / app_id / media_id).resolve()
        try:
            dest_dir.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("Media destination is outside the configured media storage root") from exc
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = (dest_dir / safe_name).resolve()
        try:
            dest.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("Media destination is outside the configured media storage root") from exc
        await asyncio.to_thread(dest.write_bytes, data)
        return str(dest)

    async def get_media(self, content_ref: str) -> bytes:
        path = self._resolve_content_ref(content_ref)
        if not path.exists() or not path.is_file():
            raise MediaContentNotFoundError(f"Local media content not found: {content_ref!r}")
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, content_ref: str) -> bool:
        try:
            path = self._resolve_content_ref(content_ref)
        except MediaContentNotFoundError:
            return False
        return path.exists() and path.is_file()

    async def delete(self, content_ref: str) -> bool:
        try:
            path = self._resolve_content_ref(content_ref)
        except MediaContentNotFoundError:
            return False
        if not path.exists() or not path.is_file():
            return False
        await asyncio.to_thread(path.unlink)
        return True


class GridFSMediaContentStore:
    """Stores generated media bytes in MongoDB GridFS."""

    backend_name = "gridfs"

    def __init__(
        self,
        *,
        mongo_uri: str | None = None,
        database: str = "mozaiksai_media",
    ) -> None:
        self._mongo_uri = mongo_uri or os.environ.get("MONGO_URI", "mongodb://localhost:27017")
        self._database = database
        self._fs: Any | None = None
        self._client: Any | None = None

    async def _ensure_fs(self) -> Any:
        if self._fs is not None:
            return self._fs
        try:
            import motor.motor_asyncio as motor  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("motor is required for GridFS media storage") from exc
        self._client = motor.AsyncIOMotorClient(self._mongo_uri)
        db = self._client[self._database]
        self._fs = motor.AsyncIOMotorGridFSBucket(db, bucket_name="media_assets")
        return self._fs

    async def put_media(
        self,
        data: bytes,
        *,
        app_id: str,
        media_id: str,
        filename: str,
        media_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        fs = await self._ensure_fs()
        safe_name = _safe_filename(filename, media_type=media_type, fallback_stem=media_id)
        file_id = await fs.upload_from_stream(
            f"{app_id}/{media_id}/{safe_name}",
            io.BytesIO(data),
            metadata={
                "app_id": app_id,
                "media_id": media_id,
                "media_type": media_type,
                **(metadata or {}),
            },
        )
        return str(file_id)

    async def get_media(self, content_ref: str) -> bytes:
        from bson import ObjectId  # type: ignore[import]

        fs = await self._ensure_fs()
        try:
            grid_out = await fs.open_download_stream(ObjectId(content_ref))
            return await grid_out.read()  # type: ignore[no-any-return]
        except Exception as exc:
            raise MediaContentNotFoundError(f"GridFS media content not found: {content_ref!r}") from exc

    async def exists(self, content_ref: str) -> bool:
        from bson import ObjectId  # type: ignore[import]

        fs = await self._ensure_fs()
        try:
            cursor = fs.find({"_id": ObjectId(content_ref)})
            docs = await cursor.to_list(length=1)
            return bool(docs)
        except Exception:
            return False

    async def delete(self, content_ref: str) -> bool:
        from bson import ObjectId  # type: ignore[import]

        fs = await self._ensure_fs()
        try:
            await fs.delete(ObjectId(content_ref))
            return True
        except Exception:
            return False


_CONTENT_BACKEND_ENV_VAR = "MOZAIKS_MEDIA_CONTENT_BACKEND"
_content_store: MediaContentStore | None = None


def get_media_content_store() -> MediaContentStore:
    """Return the process-wide generated media content store."""

    global _content_store
    if _content_store is None:
        backend = os.getenv(_CONTENT_BACKEND_ENV_VAR, "local").strip().lower()
        if backend == "gridfs":
            _content_store = GridFSMediaContentStore()
        else:
            _content_store = LocalMediaContentStore()
    return _content_store


class MediaAssetStore:
    """Mongo metadata store for generated media assets."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        content_store: MediaContentStore | None = None,
    ) -> None:
        self.client = client
        self.content_store = content_store or get_media_content_store()
        self._init_lock = asyncio.Lock()
        self._indexes_ready = False

    async def _ensure_client(self) -> None:
        if self.client is not None:
            return
        async with self._init_lock:
            if self.client is None:
                self.client = get_mongo_client()

    async def _coll(self) -> Any:
        await self._ensure_client()
        if self.client is None:
            raise RuntimeError("Mongo client not initialized")
        coll = self.client[SYSTEM_DATABASE][RuntimeCollections.MEDIA_ASSETS]
        await self._ensure_indexes(coll)
        return coll

    async def _ensure_indexes(self, coll: Any) -> None:
        if self._indexes_ready:
            return
        create_index = getattr(coll, "create_index", None)
        if callable(create_index):
            await create_index([("app_id", 1), ("asset_id", 1)], name="media_app_asset", unique=True)
            await create_index([("app_id", 1), ("source_chat_id", 1), ("created_at", -1)], name="media_app_chat_created")
            await create_index([("app_id", 1), ("kind", 1), ("created_at", -1)], name="media_app_kind_created")
        self._indexes_ready = True

    async def persist_generated_binary_result(
        self,
        binary_result: Any,
        *,
        app_id: str,
        source_workflow: str | None = None,
        source_chat_id: str | None = None,
        prompt: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        generation_params: dict[str, Any] | None = None,
        promotion_targets: Iterable[MediaPromotionTarget | str] | None = None,
    ) -> GeneratedMediaAsset:
        data = getattr(binary_result, "data", None)
        if not isinstance(data, bytes) or not data:
            raise ValueError("binary_result.data must contain bytes")

        metadata = getattr(binary_result, "metadata", None)
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        media_type = _media_type_from_metadata(metadata)
        kind = media_kind_from_mime(media_type) or MediaKind.IMAGE

        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")

        media_id = f"media_{uuid4().hex[:24]}"
        filename = _safe_filename(
            metadata.get("filename"),
            media_type=media_type,
            fallback_stem=media_id,
        )
        sha256 = hashlib.sha256(data).hexdigest()
        targets = [
            target if isinstance(target, MediaPromotionTarget) else MediaPromotionTarget(str(target))
            for target in (promotion_targets or [])
        ]
        content_ref = await self.content_store.put_media(
            data,
            app_id=resolved_app_id,
            media_id=media_id,
            filename=filename,
            media_type=media_type,
            metadata={"source_workflow": source_workflow, "source_chat_id": source_chat_id},
        )
        asset = GeneratedMediaAsset(
            _id=media_id,
            asset_id=media_id,
            app_id=resolved_app_id,
            kind=kind,
            media_type=media_type,
            filename=filename,
            size_bytes=len(data),
            sha256=sha256,
            content_ref=content_ref,
            content_backend=self.content_store.backend_name,
            source_workflow=source_workflow,
            source_chat_id=source_chat_id,
            prompt=prompt,
            provider=provider,
            model=model,
            generation_params=dict(generation_params or {}),
            promotion_targets=targets,
            metadata=metadata,
        )
        coll = await self._coll()
        await coll.insert_one(dual_write_app_scope(asset.model_dump(by_alias=True, mode="python"), resolved_app_id))
        return asset

    async def get_asset(
        self,
        *,
        app_id: str,
        asset_id: str,
    ) -> GeneratedMediaAsset | None:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        resolved_asset_id = str(asset_id or "").strip()
        if not resolved_asset_id:
            raise ValueError("asset_id is required")
        coll = await self._coll()
        doc = await coll.find_one({
            "asset_id": resolved_asset_id,
            **build_app_scope_filter(resolved_app_id),
        })
        if not isinstance(doc, dict):
            return None
        return GeneratedMediaAsset.model_validate(doc)

    def _content_store_for_backend(self, backend_name: str | None) -> MediaContentStore:
        normalized = str(backend_name or "").strip().lower()
        if not normalized or normalized == self.content_store.backend_name:
            return self.content_store
        if normalized == "gridfs":
            return GridFSMediaContentStore()
        if normalized == "local":
            return LocalMediaContentStore()
        raise ValueError(f"Unsupported media content backend: {backend_name!r}")

    async def get_asset_content(self, asset: GeneratedMediaAsset) -> bytes:
        store = self._content_store_for_backend(asset.content_backend)
        return await store.get_media(asset.content_ref)

    async def mark_asset_promoted(
        self,
        *,
        app_id: str,
        asset_id: str,
        promotion_target: MediaPromotionTarget | str,
        promoted_by: str | None = None,
    ) -> GeneratedMediaAsset | None:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        resolved_asset_id = str(asset_id or "").strip()
        if not resolved_asset_id:
            raise ValueError("asset_id is required")
        target = promotion_target if isinstance(promotion_target, MediaPromotionTarget) else MediaPromotionTarget(str(promotion_target))
        coll = await self._coll()
        now = datetime.now(UTC)
        await coll.update_one(
            {
                "asset_id": resolved_asset_id,
                **build_app_scope_filter(resolved_app_id),
            },
            {
                "$set": {
                    "metadata.promotion_status": "promoted",
                    "metadata.promoted_at": now,
                    "metadata.promoted_by": promoted_by,
                    "metadata.last_promotion_target": target.value,
                },
                "$addToSet": {"metadata.promoted_targets": target.value},
            },
        )
        return await self.get_asset(app_id=resolved_app_id, asset_id=resolved_asset_id)

    async def list_generated_assets_for_chat(
        self,
        *,
        app_id: str,
        chat_id: str,
        limit: int = 50,
    ) -> list[GeneratedMediaAsset]:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        coll = await self._coll()
        cursor = coll.find(
            {
                "source_chat_id": chat_id,
                **build_app_scope_filter(resolved_app_id),
            }
        ).sort("created_at", -1).limit(max(1, min(int(limit or 50), 200)))
        docs = await cursor.to_list(length=max(1, min(int(limit or 50), 200)))
        return [GeneratedMediaAsset.model_validate(doc) for doc in docs if isinstance(doc, dict)]


_asset_store: MediaAssetStore | None = None


def get_media_asset_store() -> MediaAssetStore:
    global _asset_store
    if _asset_store is None:
        _asset_store = MediaAssetStore()
    return _asset_store


__all__ = [
    "GridFSMediaContentStore",
    "LocalMediaContentStore",
    "MediaAssetStore",
    "MediaContentNotFoundError",
    "MediaContentStore",
    "get_media_asset_store",
    "get_media_content_store",
]
