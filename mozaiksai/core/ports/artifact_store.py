# ==============================================================================
# FILE: mozaiksai/core/ports/artifact_store.py
# DESCRIPTION: Port (contract) for artifact object storage.
#              Decouples artifact write/read from MongoDB document storage.
#              Large artifacts (app bundles, workflow bundles) stream to object
#              storage; MongoDB holds only the metadata + URL.
#
#              Implementations:
#                - LocalArtifactStore  — filesystem (default, development)
#                - S3ArtifactStore     — AWS S3 or S3-compatible (production)
#                - MongoArtifactStore  — inline MongoDB (small artifacts, not recommended for production)
# ==============================================================================
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ArtifactStore(Protocol):
    """Port for reading and writing named artifact blobs."""

    async def write(
        self,
        *,
        artifact_id: str,
        artifact_kind: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Write an artifact blob and return its canonical URL/path."""
        ...

    async def read(self, *, artifact_id: str, artifact_kind: str) -> bytes | None:
        """Read an artifact blob by id. Returns None if not found."""
        ...

    async def delete(self, *, artifact_id: str, artifact_kind: str) -> bool:
        """Delete an artifact blob. Returns True if deleted."""
        ...

    async def exists(self, *, artifact_id: str, artifact_kind: str) -> bool:
        """Check whether an artifact blob exists."""
        ...

    def url_for(self, *, artifact_id: str, artifact_kind: str) -> str:
        """Return the canonical URL or path for an artifact (may not exist yet)."""
        ...


# ---------------------------------------------------------------------------
# Local filesystem implementation (default / development)
# ---------------------------------------------------------------------------

class LocalArtifactStore:
    """Stores artifacts on the local filesystem.

    Suitable for development and single-instance deployments.
    Use S3ArtifactStore for multi-instance production.

    Configuration (env vars):
        MOZAIKS_ARTIFACT_STORE_PATH  — base directory (default: ./artifacts)
    """

    def __init__(self, base_path: str | Path | None = None) -> None:
        raw: str | Path = base_path or os.getenv("MOZAIKS_ARTIFACT_STORE_PATH") or "artifacts"
        self._base = Path(raw).resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    def _artifact_path(self, artifact_id: str, artifact_kind: str) -> Path:
        kind_dir = self._base / artifact_kind
        kind_dir.mkdir(parents=True, exist_ok=True)
        return kind_dir / artifact_id

    async def write(
        self,
        *,
        artifact_id: str,
        artifact_kind: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        path = self._artifact_path(artifact_id, artifact_kind)
        path.write_bytes(content)
        return str(path)

    async def read(self, *, artifact_id: str, artifact_kind: str) -> bytes | None:
        path = self._artifact_path(artifact_id, artifact_kind)
        if not path.exists():
            return None
        return path.read_bytes()

    async def delete(self, *, artifact_id: str, artifact_kind: str) -> bool:
        path = self._artifact_path(artifact_id, artifact_kind)
        if path.exists():
            path.unlink()
            return True
        return False

    async def exists(self, *, artifact_id: str, artifact_kind: str) -> bool:
        return self._artifact_path(artifact_id, artifact_kind).exists()

    def url_for(self, *, artifact_id: str, artifact_kind: str) -> str:
        return str(self._artifact_path(artifact_id, artifact_kind))


# ---------------------------------------------------------------------------
# S3 adapter stub (requires boto3 or aiobotocore)
# ---------------------------------------------------------------------------

class S3ArtifactStore:
    """Stores artifacts in AWS S3 or an S3-compatible store (MinIO, R2, etc.).

    Configuration (env vars):
        ARTIFACT_S3_BUCKET           — required: S3 bucket name
        ARTIFACT_S3_PREFIX           — optional: key prefix (default: "artifacts")
        ARTIFACT_S3_REGION           — optional: AWS region
        ARTIFACT_S3_ENDPOINT_URL     — optional: custom endpoint (MinIO, R2)
        AWS_ACCESS_KEY_ID            — AWS credentials (or IAM role)
        AWS_SECRET_ACCESS_KEY        — AWS credentials
    """

    def __init__(self) -> None:
        self._bucket = os.getenv("ARTIFACT_S3_BUCKET", "")
        self._prefix = os.getenv("ARTIFACT_S3_PREFIX", "artifacts").strip("/")
        self._region = os.getenv("ARTIFACT_S3_REGION", "us-east-1")
        self._endpoint = os.getenv("ARTIFACT_S3_ENDPOINT_URL")
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
                kwargs: dict[str, Any] = {"region_name": self._region}
                if self._endpoint:
                    kwargs["endpoint_url"] = self._endpoint
                self._client = boto3.client("s3", **kwargs)
            except ImportError as exc:
                raise RuntimeError(
                    "boto3 is required for S3ArtifactStore. "
                    "Install it: pip install boto3"
                ) from exc
        return self._client

    def _key(self, artifact_id: str, artifact_kind: str) -> str:
        return f"{self._prefix}/{artifact_kind}/{artifact_id}"

    async def write(
        self,
        *,
        artifact_id: str,
        artifact_kind: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        import asyncio
        client = self._ensure_client()
        key = self._key(artifact_id, artifact_kind)
        kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": content,
            "ContentType": content_type,
        }
        if metadata:
            kwargs["Metadata"] = {k: str(v) for k, v in metadata.items()}
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.put_object(**kwargs)
        )
        return self.url_for(artifact_id=artifact_id, artifact_kind=artifact_kind)

    async def read(self, *, artifact_id: str, artifact_kind: str) -> bytes | None:
        import asyncio
        client = self._ensure_client()
        key = self._key(artifact_id, artifact_kind)
        try:
            resp = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.get_object(Bucket=self._bucket, Key=key)
            )
            return bytes(resp["Body"].read())
        except Exception:
            return None

    async def delete(self, *, artifact_id: str, artifact_kind: str) -> bool:
        import asyncio
        client = self._ensure_client()
        key = self._key(artifact_id, artifact_kind)
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.delete_object(Bucket=self._bucket, Key=key)
            )
            return True
        except Exception:
            return False

    async def exists(self, *, artifact_id: str, artifact_kind: str) -> bool:
        import asyncio
        client = self._ensure_client()
        key = self._key(artifact_id, artifact_kind)
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.head_object(Bucket=self._bucket, Key=key)
            )
            return True
        except Exception:
            return False

    def url_for(self, *, artifact_id: str, artifact_kind: str) -> str:
        key = self._key(artifact_id, artifact_kind)
        if self._endpoint:
            return f"{self._endpoint.rstrip('/')}/{self._bucket}/{key}"
        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_artifact_store: ArtifactStore | None = None


def get_artifact_store() -> ArtifactStore:
    """Return the configured artifact store (process-wide singleton).

    Selects backend based on ARTIFACT_STORE_BACKEND env var:
        local  — LocalArtifactStore (default)
        s3     — S3ArtifactStore
    """
    global _artifact_store
    if _artifact_store is None:
        backend = os.getenv("ARTIFACT_STORE_BACKEND", "local").strip().lower()
        if backend == "s3":
            _artifact_store = S3ArtifactStore()
        else:
            _artifact_store = LocalArtifactStore()
    return _artifact_store


__all__ = [
    "ArtifactStore",
    "LocalArtifactStore",
    "S3ArtifactStore",
    "get_artifact_store",
]
