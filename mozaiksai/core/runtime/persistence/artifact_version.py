# ==============================================================================
# FILE: mozaiksai/core/runtime/persistence/artifact_version.py
# DESCRIPTION: Artifact versioning contract.
#              Tracks artifact ID, version, lineage, and parent_build_id so
#              Studio can show artifact history, diff between versions, and
#              roll back to a previous build.
#
# Every promoted artifact gets a version record in artifact_versions collection.
# ==============================================================================
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from logs.logging_config import get_core_logger

logger = get_core_logger("artifact_version")

_VERSIONS_COLLECTION = "artifact_versions"
_VERSIONS_DB = os.getenv("MOZAIKS_APP_DATABASE_NAME", "mozaiks_apps")

# Canonical artifact kinds (matches AppGenerator output)
ARTIFACT_KINDS = frozenset({
    "app_bundle",
    "workflow_bundle",
    "module",
    "page",
    "data_contract",
    "deployment_manifest",
    "theme",
    "design_docs",
    "agent_config",
    "app_context",
})


@dataclass
class ArtifactVersion:
    """Immutable version record for a promoted artifact."""

    artifact_id: str
    artifact_kind: str
    version: int
    app_id: str
    build_id: str
    parent_version: int | None = None
    parent_build_id: str | None = None
    promoted_by: str = "system"
    store_url: str | None = None          # Object store URL or filesystem path
    checksum: str | None = None           # SHA-256 of artifact content
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    record_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.record_id,
            "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind,
            "version": self.version,
            "app_id": self.app_id,
            "build_id": self.build_id,
            "parent_version": self.parent_version,
            "parent_build_id": self.parent_build_id,
            "promoted_by": self.promoted_by,
            "store_url": self.store_url,
            "checksum": self.checksum,
            "tags": self.tags,
            "extra": self.extra,
            "created_at": self.created_at,
        }


def _get_collection() -> Any | None:
    try:
        from mozaiksai.core.core_config import get_mongo_client
        client = get_mongo_client()
        if client is None:
            return None
        return client[_VERSIONS_DB][_VERSIONS_COLLECTION]
    except Exception as exc:
        logger.debug("Artifact versions collection unavailable: %s", exc)
        return None


async def ensure_artifact_version_indexes() -> None:
    """Create lookup indexes. Call once at startup."""
    collection = _get_collection()
    if collection is None:
        return
    try:
        await collection.create_index(
            [("app_id", 1), ("artifact_id", 1), ("version", -1)],
            name="artifact_version_lookup_idx",
            background=True,
        )
        await collection.create_index(
            [("app_id", 1), ("build_id", 1)],
            name="artifact_build_idx",
            background=True,
        )
        logger.debug("Artifact version indexes ensured")
    except Exception as exc:
        logger.warning("Could not ensure artifact version indexes: %s", exc)


async def record_artifact_version(version: ArtifactVersion) -> None:
    """Persist an artifact version record. Failures are logged, never raised."""
    collection = _get_collection()
    if collection is None:
        logger.debug(
            "ARTIFACT_VERSION_SKIP artifact_id=%s version=%d — MongoDB unavailable",
            version.artifact_id, version.version,
        )
        return
    try:
        await collection.insert_one(version.to_document())
        logger.info(
            "ARTIFACT_PROMOTED artifact_id=%s kind=%s version=%d build_id=%s",
            version.artifact_id, version.artifact_kind, version.version, version.build_id,
        )
    except Exception as exc:
        logger.error(
            "ARTIFACT_VERSION_WRITE_FAIL artifact_id=%s: %s",
            version.artifact_id, exc,
        )


async def get_artifact_history(
    *,
    app_id: str,
    artifact_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return version history for an artifact, newest first."""
    collection = _get_collection()
    if collection is None:
        return []
    try:
        cursor = collection.find(
            {"app_id": app_id, "artifact_id": artifact_id},
            sort=[("version", -1)],
        ).limit(limit)
        return list(await cursor.to_list(length=limit))  # type: ignore[arg-type]
    except Exception as exc:
        logger.error("get_artifact_history failed artifact_id=%s: %s", artifact_id, exc)
        return []


async def get_latest_artifact_version(
    *,
    app_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    """Return the most recent version record for an artifact."""
    collection = _get_collection()
    if collection is None:
        return None
    try:
        result = await collection.find_one(
            {"app_id": app_id, "artifact_id": artifact_id},
            sort=[("version", -1)],
        )
        return dict(result) if result is not None else None
    except Exception as exc:
        logger.error("get_latest_artifact_version failed artifact_id=%s: %s", artifact_id, exc)
        return None


async def next_version_number(*, app_id: str, artifact_id: str) -> int:
    """Return the next sequential version number for an artifact (1-based)."""
    latest = await get_latest_artifact_version(app_id=app_id, artifact_id=artifact_id)
    if latest is None:
        return 1
    return int(latest.get("version", 0)) + 1


__all__ = [
    "ARTIFACT_KINDS",
    "ArtifactVersion",
    "ensure_artifact_version_indexes",
    "get_artifact_history",
    "get_latest_artifact_version",
    "next_version_number",
    "record_artifact_version",
]
