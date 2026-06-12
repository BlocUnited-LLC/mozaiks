from __future__ import annotations

"""Generic durable store helpers for workflow human-review artifacts."""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_REVIEW_ARTIFACT_NAMESPACE = "workflow_review_artifacts"
DEFAULT_ID_FIELDS: tuple[str, ...] = ("artifact_id", "proposal_id", "review_id", "id")

_SUMMARY_PROJECTION = {
    "_id": 0,
    "artifact": 0,
    "proposal": 0,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_namespace(namespace: str | None) -> str:
    resolved = str(namespace or DEFAULT_REVIEW_ARTIFACT_NAMESPACE).strip()
    if not resolved:
        return DEFAULT_REVIEW_ARTIFACT_NAMESPACE
    return resolved


def _resolve_artifact_id(
    artifact: dict[str, Any],
    *,
    id_fields: Sequence[str] = DEFAULT_ID_FIELDS,
) -> str:
    for field in id_fields:
        value = str(artifact.get(field) or "").strip()
        if value:
            return value
    return ""


def _resolve_review_url(
    artifact: dict[str, Any],
    *,
    artifact_id: str,
    review_url: str | None,
    review_url_template: str | None,
) -> str:
    explicit = str(review_url or artifact.get("review_url") or "").strip()
    if explicit:
        return explicit
    template = str(review_url_template or "").strip()
    if template:
        return template.format(artifact_id=artifact_id)
    return ""


async def save_review_artifact(
    artifact: dict[str, Any],
    ctx: Any = None,
    *,
    namespace: str | None = None,
    source: str = "workflow",
    warnings: list[str] | None = None,
    status: str = "proposed",
    review_task_status: str = "open",
    review_required: bool = True,
    review_url: str | None = None,
    review_url_template: str | None = None,
    id_fields: Sequence[str] = DEFAULT_ID_FIELDS,
) -> None:
    """Persist a workflow review artifact to ``ctx.db`` when available.

    This helper is intentionally non-fatal. Workflow runs should keep their
    in-session artifact even when durable storage is unavailable.
    """

    if not isinstance(artifact, dict):
        logger.warning("workflow review artifact is not an object; skipping")
        return

    artifact_id = _resolve_artifact_id(artifact, id_fields=id_fields)
    if not artifact_id:
        logger.warning("workflow review artifact id is empty; skipping")
        return

    if ctx is None:
        logger.warning("no ctx; workflow review artifact %s not persisted", artifact_id)
        return

    db = getattr(ctx, "db", None)
    if db is None:
        logger.warning("ctx.db is None; workflow review artifact %s not persisted", artifact_id)
        return

    collection = _resolve_namespace(namespace)
    now = _now()
    try:
        doc: dict[str, Any] = {
            "doc_id": artifact_id,
            "artifact_id": artifact_id,
            "app_id": artifact.get("app_id", ""),
            "community_id": artifact.get("community_id", ""),
            "artifact": artifact,
            "proposal": artifact,
            "status": status,
            "review_required": review_required,
            "review_task_status": review_task_status,
            "source": source,
            "warnings": list(warnings or []),
            "updated_at": now,
            "review_url": _resolve_review_url(
                artifact,
                artifact_id=artifact_id,
                review_url=review_url,
                review_url_template=review_url_template,
            ),
        }
        await db[collection].update_one(
            {"doc_id": artifact_id},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        logger.info("saved workflow review artifact id=%s collection=%s", artifact_id, collection)
    except Exception as exc:
        logger.warning("workflow review artifact write failed id=%s: %s", artifact_id, exc)


async def get_review_artifact(
    artifact_id: str,
    ctx: Any = None,
    *,
    namespace: str | None = None,
) -> dict[str, Any] | None:
    """Load a workflow review artifact from ``ctx.db`` when available."""

    resolved_id = str(artifact_id or "").strip()
    if not resolved_id or ctx is None:
        return None

    db = getattr(ctx, "db", None)
    if db is None:
        return None

    collection = _resolve_namespace(namespace)
    try:
        doc = await db[collection].find_one({"doc_id": resolved_id}, {"_id": 0})
        if not isinstance(doc, dict):
            return None
        artifact = doc.get("artifact") or doc.get("proposal")
        return artifact if isinstance(artifact, dict) else None
    except Exception as exc:
        logger.warning("workflow review artifact read failed id=%s: %s", resolved_id, exc)
        return None


async def list_review_artifacts(
    ctx: Any = None,
    *,
    namespace: str | None = None,
    status: str | None = None,
    review_task_status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return workflow review artifact summaries from ``ctx.db``."""

    if ctx is None:
        return []

    db = getattr(ctx, "db", None)
    if db is None:
        return []

    resolved_limit = max(1, min(int(limit or 50), 200))
    collection = _resolve_namespace(namespace)
    try:
        query: dict[str, Any] = {}
        if status:
            query["status"] = status
        if review_task_status:
            query["review_task_status"] = review_task_status
        cursor = (
            db[collection]
            .find(query, _SUMMARY_PROJECTION)
            .sort("created_at", -1)
            .limit(resolved_limit)
        )
        return await cursor.to_list(length=resolved_limit)
    except Exception as exc:
        logger.warning("workflow review artifact list failed collection=%s: %s", collection, exc)
        return []


async def update_review_artifact_status(
    artifact_id: str,
    ctx: Any = None,
    *,
    namespace: str | None = None,
    status: str,
    review_task_status: str | None = None,
    catalog_id: str | None = None,
    catalog_version: str | None = None,
) -> bool:
    """Update review status fields for an existing workflow review artifact."""

    resolved_id = str(artifact_id or "").strip()
    if not resolved_id or ctx is None:
        return False

    db = getattr(ctx, "db", None)
    if db is None:
        return False

    collection = _resolve_namespace(namespace)
    try:
        fields: dict[str, Any] = {
            "status": status,
            "updated_at": _now(),
        }
        if review_task_status is not None:
            fields["review_task_status"] = review_task_status
        if catalog_id is not None:
            fields["catalog_id"] = catalog_id
        if catalog_version is not None:
            fields["catalog_version"] = catalog_version

        await db[collection].update_one({"doc_id": resolved_id}, {"$set": fields})
        logger.info(
            "updated workflow review artifact id=%s status=%s review_task_status=%s",
            resolved_id,
            status,
            review_task_status,
        )
        return True
    except Exception as exc:
        logger.warning("workflow review artifact status update failed id=%s: %s", resolved_id, exc)
        return False
