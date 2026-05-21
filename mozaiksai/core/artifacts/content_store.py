from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
from typing import Any, Optional, runtime_checkable

from typing import Protocol


class ContentNotFoundError(Exception):
    """Raised when a content_ref does not resolve to stored content."""


@runtime_checkable
class ArtifactContentStore(Protocol):
    """Pluggable content backend for artifact bundle storage.

    Implementations must supply five async methods and a ``backend_name``
    class attribute.  ``content_ref`` is an opaque string whose format is
    backend-specific (e.g. an absolute path for ``local``, a GridFS ObjectId
    string for ``gridfs``).
    """

    backend_name: str

    async def put_bundle(self, data: bytes, *, app_id: str, artifact_version_id: str) -> str:
        """Store bundle bytes and return an opaque ``content_ref`` string."""
        ...

    async def get_bundle(self, content_ref: str) -> bytes:
        """Retrieve bundle bytes by ``content_ref``.

        Raises :class:`ContentNotFoundError` if the ref does not resolve.
        """
        ...

    async def exists(self, content_ref: str) -> bool:
        """Return ``True`` if ``content_ref`` resolves to stored content."""
        ...

    async def verify_checksum(self, content_ref: str, sha256: str) -> bool:
        """Return ``True`` if the stored content matches the expected SHA-256 hex digest."""
        ...

    async def delete(self, content_ref: str) -> bool:
        """Delete the content addressed by ``content_ref``.

        Returns ``True`` if content was found and deleted, ``False`` if it
        did not exist.  Never raises on a missing ref.
        """
        ...


class LocalArtifactContentStore:
    """Stores artifact bundle zips on the local filesystem.

    ``content_ref`` is the absolute path string of the stored zip file.  This
    is structurally identical to the pre-Phase-D ``artifact_path`` metadata
    field, so the local backend remains fully backward-compatible with
    artifacts written before Phase D.

    The ``put_bundle`` method writes under ``<root>/<app_id>/<artifact_version_id>/artifact.zip``
    and returns the resolved absolute path.
    """

    backend_name: str = "local"

    def __init__(self, root: Optional[Path | str] = None) -> None:
        self._root = Path(root) if root else Path("generated_artifacts")

    async def put_bundle(self, data: bytes, *, app_id: str, artifact_version_id: str) -> str:
        dest_dir = self._root / app_id / artifact_version_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "artifact.zip"
        dest.write_bytes(data)
        return str(dest.resolve())

    async def get_bundle(self, content_ref: str) -> bytes:
        path = Path(content_ref)
        if not path.exists():
            raise ContentNotFoundError(f"Local content not found: {content_ref!r}")
        return path.read_bytes()

    async def exists(self, content_ref: str) -> bool:
        return Path(content_ref).exists()

    async def verify_checksum(self, content_ref: str, sha256: str) -> bool:
        try:
            data = await self.get_bundle(content_ref)
        except ContentNotFoundError:
            return False
        return hashlib.sha256(data).hexdigest() == sha256

    async def delete(self, content_ref: str) -> bool:
        path = Path(content_ref)
        if not path.exists():
            return False
        path.unlink()
        return True


class GridFSArtifactContentStore:
    """Stores artifact bundle zips in MongoDB GridFS.

    ``content_ref`` is the GridFS file ``_id`` rendered as a string.

    Requires the ``motor`` async MongoDB driver.  Import errors are deferred
    to the first ``_ensure_fs()`` call so that this class can be instantiated
    without motor installed (useful for config validation at startup).

    The GridFS bucket is named ``artifact_bundles`` inside the configured
    ``database``.  Each upload is tagged with ``app_id`` and
    ``artifact_version_id`` as GridFS file metadata.
    """

    backend_name: str = "gridfs"

    def __init__(
        self,
        *,
        mongo_uri: Optional[str] = None,
        database: str = "mozaiksai_artifacts",
    ) -> None:
        self._mongo_uri = mongo_uri or os.environ.get("MONGO_URI", "mongodb://localhost:27017")
        self._database = database
        self._fs: Optional[Any] = None
        self._client: Optional[Any] = None

    async def _ensure_fs(self) -> Any:
        if self._fs is not None:
            return self._fs
        try:
            import motor.motor_asyncio as motor  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "motor is required for the GridFS artifact content store. "
                "Install it with: pip install motor"
            ) from exc
        self._client = motor.AsyncIOMotorClient(self._mongo_uri)
        db = self._client[self._database]
        self._fs = motor.AsyncIOMotorGridFSBucket(db, bucket_name="artifact_bundles")
        return self._fs

    async def put_bundle(self, data: bytes, *, app_id: str, artifact_version_id: str) -> str:
        fs = await self._ensure_fs()
        file_id = await fs.upload_from_stream(
            f"{app_id}/{artifact_version_id}/artifact.zip",
            io.BytesIO(data),
            metadata={"app_id": app_id, "artifact_version_id": artifact_version_id},
        )
        return str(file_id)

    async def get_bundle(self, content_ref: str) -> bytes:
        from bson import ObjectId  # type: ignore[import]

        fs = await self._ensure_fs()
        try:
            grid_out = await fs.open_download_stream(ObjectId(content_ref))
            return await grid_out.read()
        except Exception as exc:
            raise ContentNotFoundError(f"GridFS content not found: {content_ref!r}") from exc

    async def exists(self, content_ref: str) -> bool:
        from bson import ObjectId  # type: ignore[import]

        fs = await self._ensure_fs()
        try:
            cursor = fs.find({"_id": ObjectId(content_ref)})
            docs = await cursor.to_list(length=1)
            return bool(docs)
        except Exception:
            return False

    async def verify_checksum(self, content_ref: str, sha256: str) -> bool:
        try:
            data = await self.get_bundle(content_ref)
        except ContentNotFoundError:
            return False
        return hashlib.sha256(data).hexdigest() == sha256

    async def delete(self, content_ref: str) -> bool:
        from bson import ObjectId  # type: ignore[import]

        fs = await self._ensure_fs()
        try:
            await fs.delete(ObjectId(content_ref))
            return True
        except Exception:
            return False


_CONTENT_BACKEND_ENV_VAR = "MOZAIKS_ARTIFACT_CONTENT_BACKEND"

_content_store: Optional[ArtifactContentStore] = None


def get_artifact_content_store() -> ArtifactContentStore:
    """Return the process-wide artifact content store singleton.

    Backend selection is driven by the ``MOZAIKS_ARTIFACT_CONTENT_BACKEND``
    environment variable:

    - ``"local"`` (default) — :class:`LocalArtifactContentStore`
    - ``"gridfs"`` — :class:`GridFSArtifactContentStore`

    Unknown values fall back to ``"local"``.
    """
    global _content_store
    if _content_store is None:
        backend = os.environ.get(_CONTENT_BACKEND_ENV_VAR, "local").strip().lower()
        if backend == "gridfs":
            _content_store = GridFSArtifactContentStore()
        else:
            _content_store = LocalArtifactContentStore()
    return _content_store


__all__ = [
    "ContentNotFoundError",
    "ArtifactContentStore",
    "LocalArtifactContentStore",
    "GridFSArtifactContentStore",
    "get_artifact_content_store",
]
