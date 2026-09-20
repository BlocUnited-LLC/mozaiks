from __future__ import annotations

import hashlib
import io
import logging
import os
import re
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable
from uuid import uuid4

logger = logging.getLogger(__name__)


class ContentNotFoundError(Exception):
    """Raised when a content_ref does not resolve to stored content."""


class ContentIntegrityError(ValueError):
    """Raised when immutable content does not match its claimed digest."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _verified_blob_digest(data: bytes, expected_digest: str) -> str:
    digest = str(expected_digest or "")
    if _SHA256.fullmatch(digest) is None:
        raise ContentIntegrityError("expected_digest must be a lowercase SHA-256 digest")
    actual = hashlib.sha256(data).hexdigest()
    if actual != digest:
        raise ContentIntegrityError("exact blob bytes do not match expected_digest")
    return digest


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

    async def put_blob(self, data: bytes, *, expected_digest: str) -> str:
        """Idempotently persist exact immutable bytes under their SHA-256 identity."""
        ...

    async def get_verified_blob(self, content_digest: str) -> bytes:
        """Return exact bytes only after recomputing their content digest."""
        ...

    async def has_verified_blob(self, content_digest: str) -> bool:
        """Return whether an exact, digest-verified immutable blob exists."""
        ...


class LocalArtifactContentStore:
    """Stores artifact bundle zips on the local filesystem.

    ``content_ref`` is the absolute path string of the stored zip file. The
    local backend keeps bundle storage inspectable for development and tests.

    The ``put_bundle`` method writes under ``<root>/<app_id>/<artifact_version_id>/artifact.zip``
    and returns the resolved absolute path.
    """

    backend_name: str = "local"

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) if root else Path("generated_artifacts")

    async def put_bundle(
        self,
        data: bytes,
        *,
        app_id: str,
        artifact_version_id: str = "",
        build_record_id: str = "",
    ) -> str:
        record_id = build_record_id or artifact_version_id
        dest_dir = self._root / app_id / record_id
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

    def _blob_path(self, content_digest: str) -> Path:
        digest = str(content_digest or "")
        if _SHA256.fullmatch(digest) is None:
            raise ContentIntegrityError("content_digest must be a lowercase SHA-256 digest")
        return self._root / "sha256" / digest[:2] / digest

    async def put_blob(self, data: bytes, *, expected_digest: str) -> str:
        digest = _verified_blob_digest(data, expected_digest)
        destination = self._blob_path(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{digest}.{uuid4().hex}.tmp"
        temporary.write_bytes(data)
        try:
            try:
                os.link(temporary, destination)
            except FileExistsError:
                existing = destination.read_bytes()
                _verified_blob_digest(existing, digest)
                if existing != data:
                    raise ContentIntegrityError(
                        "immutable blob identity resolved to different exact bytes"
                    ) from None
        finally:
            temporary.unlink(missing_ok=True)
        return digest

    async def get_verified_blob(self, content_digest: str) -> bytes:
        path = self._blob_path(content_digest)
        if not path.is_file():
            raise ContentNotFoundError(f"Immutable blob not found: {content_digest!r}")
        data = path.read_bytes()
        _verified_blob_digest(data, content_digest)
        return cast(bytes, data)

    async def has_verified_blob(self, content_digest: str) -> bool:
        try:
            await self.get_verified_blob(content_digest)
        except ContentNotFoundError:
            return False
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
        mongo_uri: str | None = None,
        database: str = "mozaiksai_artifacts",
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
            raise RuntimeError(
                "motor is required for the GridFS artifact content store. "
                "Install it with: pip install motor"
            ) from exc
        self._client = motor.AsyncIOMotorClient(self._mongo_uri)
        db = self._client[self._database]
        self._fs = motor.AsyncIOMotorGridFSBucket(db, bucket_name="artifact_bundles")
        return self._fs

    async def put_bundle(
        self,
        data: bytes,
        *,
        app_id: str,
        artifact_version_id: str = "",
        build_record_id: str = "",
    ) -> str:
        record_id = build_record_id or artifact_version_id
        fs = await self._ensure_fs()
        file_id = await fs.upload_from_stream(
            f"{app_id}/{record_id}/artifact.zip",
            io.BytesIO(data),
            metadata={"app_id": app_id, "artifact_version_id": record_id},
        )
        return str(file_id)

    async def get_bundle(self, content_ref: str) -> bytes:
        from bson import ObjectId  # type: ignore[import]

        fs = await self._ensure_fs()
        try:
            grid_out = await fs.open_download_stream(ObjectId(content_ref))
            return await grid_out.read()  # type: ignore[no-any-return]
        except Exception as exc:
            raise ContentNotFoundError(f"GridFS content not found: {content_ref!r}") from exc

    async def exists(self, content_ref: str) -> bool:
        from bson import ObjectId  # type: ignore[import]

        fs = await self._ensure_fs()
        try:
            cursor = fs.find({"_id": ObjectId(content_ref)})
            docs = await cursor.to_list(length=1)
            return bool(docs)
        except Exception as exc:
            logger.error(
                "CONTENT_STORE_EXISTS_FAILED content_ref=%s: %s — returning False (storage may be unreachable)",
                content_ref,
                exc,
                exc_info=True,
            )
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
        except Exception as exc:
            logger.error(
                "CONTENT_STORE_DELETE_FAILED content_ref=%s: %s — returning False (bundle may remain in storage)",
                content_ref,
                exc,
                exc_info=True,
            )
            return False

    async def put_blob(self, data: bytes, *, expected_digest: str) -> str:
        from pymongo.errors import DuplicateKeyError

        digest = _verified_blob_digest(data, expected_digest)
        fs = await self._ensure_fs()
        try:
            await fs.upload_from_stream_with_id(
                digest,
                digest,
                io.BytesIO(data),
                metadata={"content_digest": digest, "immutable": True},
            )
        except DuplicateKeyError:
            existing = await self.get_verified_blob(digest)
            if existing != data:
                raise ContentIntegrityError(
                    "immutable GridFS blob identity resolved to different exact bytes"
                ) from None
        return digest

    async def get_verified_blob(self, content_digest: str) -> bytes:
        from gridfs.errors import NoFile  # type: ignore[import]

        if _SHA256.fullmatch(str(content_digest or "")) is None:
            raise ContentIntegrityError("content_digest must be a lowercase SHA-256 digest")
        fs = await self._ensure_fs()
        try:
            grid_out = await fs.open_download_stream(content_digest)
            data = await grid_out.read()
        except NoFile as exc:
            raise ContentNotFoundError(
                f"Immutable GridFS blob not found: {content_digest!r}"
            ) from exc
        _verified_blob_digest(data, content_digest)
        return cast(bytes, data)

    async def has_verified_blob(self, content_digest: str) -> bool:
        try:
            await self.get_verified_blob(content_digest)
        except ContentNotFoundError:
            return False
        return True


_CONTENT_BACKEND_ENV_VAR = "MOZAIKS_ARTIFACT_CONTENT_BACKEND"
_BUNDLE_CONTENT_BACKEND_ENV_VAR = "MOZAIKS_BUNDLE_CONTENT_BACKEND"

_content_store: ArtifactContentStore | None = None
_bundle_content_store: ArtifactContentStore | None = None


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


def get_bundle_content_store() -> ArtifactContentStore:
    """Return the process-wide bundle content store singleton.

    Backend selection is driven by the ``MOZAIKS_BUNDLE_CONTENT_BACKEND``
    environment variable:

    - ``"local"`` (default) — :class:`LocalArtifactContentStore`
    - ``"gridfs"`` — :class:`GridFSArtifactContentStore`

    Unknown values fall back to ``"local"``.
    """
    global _bundle_content_store
    if _bundle_content_store is None:
        backend = os.environ.get(_BUNDLE_CONTENT_BACKEND_ENV_VAR, "local").strip().lower()
        if backend == "gridfs":
            _bundle_content_store = GridFSArtifactContentStore()
        else:
            _bundle_content_store = LocalArtifactContentStore()
    return _bundle_content_store


# New-API name aliases (bundle naming)
BundleContentStore = ArtifactContentStore
LocalBundleContentStore = LocalArtifactContentStore
GridFSBundleContentStore = GridFSArtifactContentStore


__all__ = [
    "ContentIntegrityError",
    "ContentNotFoundError",
    "ArtifactContentStore",
    "LocalArtifactContentStore",
    "GridFSArtifactContentStore",
    "get_artifact_content_store",
    "BundleContentStore",
    "LocalBundleContentStore",
    "GridFSBundleContentStore",
    "get_bundle_content_store",
]
