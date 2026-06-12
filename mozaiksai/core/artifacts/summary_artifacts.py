from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from .models import ArtifactLifecycleStatus, ArtifactValidationStatus, ArtifactVersionDoc
from .store import ArtifactStore, get_artifact_store

logger = logging.getLogger(__name__)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="python")
        except Exception:
            return str(value)
    return str(value)


def _summary_bytes(summary_payload: Any) -> bytes:
    return json.dumps(
        summary_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    ).encode("utf-8")


def extract_summary_payload(artifact: ArtifactVersionDoc | Mapping[str, Any] | None) -> Any:
    if artifact is None:
        return None
    commit_metadata = getattr(artifact, "commit_metadata", None)
    metadata = getattr(commit_metadata, "metadata", None)
    if isinstance(metadata, dict):
        return metadata.get("summary_payload")
    if isinstance(artifact, Mapping):
        raw_commit = artifact.get("commit_metadata")
        if isinstance(raw_commit, Mapping):
            raw_metadata = raw_commit.get("metadata")
            if isinstance(raw_metadata, Mapping):
                return raw_metadata.get("summary_payload")
    return None


async def resolve_latest_artifact_version_refs(
    *,
    app_id: str,
    artifact_kinds: Iterable[str],
    artifact_key_by_kind: Mapping[str, str] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, str]:
    resolved_app_id = str(app_id or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")

    store = artifact_store or get_artifact_store()
    resolved_refs: dict[str, str] = {}
    seen_kinds: set[str] = set()

    for raw_kind in artifact_kinds:
        artifact_kind = str(raw_kind or "").strip()
        if not artifact_kind or artifact_kind in seen_kinds:
            continue
        seen_kinds.add(artifact_kind)

        artifact_key = str((artifact_key_by_kind or {}).get(artifact_kind) or artifact_kind).strip() or None
        versions = await store.list_artifact_versions(
            app_id=resolved_app_id,
            artifact_kind=artifact_kind,
            artifact_key=artifact_key,
            lifecycle_status=ArtifactLifecycleStatus.CURRENT,
            limit=1,
        )
        if not versions and artifact_key is not None:
            versions = await store.list_artifact_versions(
                app_id=resolved_app_id,
                artifact_kind=artifact_kind,
                lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                limit=1,
            )
        if versions:
            resolved_refs[artifact_kind] = versions[0].id
        else:
            logger.debug(
                "[ARTIFACTS] No CURRENT version found for kind=%r app=%s — "
                "canonical input absent (first-run or all versions are DRAFT/STALE).",
                artifact_kind,
                resolved_app_id,
            )

    return resolved_refs


async def persist_summary_artifact(
    *,
    app_id: str,
    artifact_kind: str,
    summary_payload: Any,
    source_workflow: str | None = None,
    source_chat_id: str | None = None,
    author_user_id: str | None = None,
    artifact_key: str | None = None,
    message: str | None = None,
    revision_mode: bool = False,
    parent_version_id: str | None = None,
    canonical_inputs_version: Mapping[str, str] | None = None,
    input_artifact_kinds: Iterable[str] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> ArtifactVersionDoc:
    resolved_app_id = str(app_id or "").strip()
    resolved_artifact_kind = str(artifact_kind or "").strip()
    resolved_artifact_key = str(artifact_key or artifact_kind or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")
    if not resolved_artifact_kind:
        raise ValueError("artifact_kind is required")
    if not resolved_artifact_key:
        raise ValueError("artifact_key is required")

    store = artifact_store or get_artifact_store()
    resolved_parent_version_id = str(parent_version_id or "").strip() or None
    if not resolved_parent_version_id:
        prior_versions = await store.list_artifact_versions(
            app_id=resolved_app_id,
            artifact_kind=resolved_artifact_kind,
            artifact_key=resolved_artifact_key,
            lifecycle_status=ArtifactLifecycleStatus.CURRENT,
            limit=1,
        )
        if prior_versions:
            resolved_parent_version_id = prior_versions[0].id

    resolved_inputs = await resolve_latest_artifact_version_refs(
        app_id=resolved_app_id,
        artifact_kinds=input_artifact_kinds or (),
        artifact_store=store,
    )
    for input_kind, version_id in dict(canonical_inputs_version or {}).items():
        normalized_kind = str(input_kind or "").strip()
        normalized_version_id = str(version_id or "").strip()
        if normalized_kind and normalized_version_id:
            resolved_inputs[normalized_kind] = normalized_version_id

    summary_bytes = _summary_bytes(summary_payload)
    lifecycle_status = (
        ArtifactLifecycleStatus.DRAFT
        if revision_mode and resolved_parent_version_id
        else ArtifactLifecycleStatus.CURRENT
    )
    summary_file_name = f"{resolved_artifact_key}.json"

    return await store.create_artifact_version(
        app_id=resolved_app_id,
        artifact_kind=resolved_artifact_kind,
        artifact_key=resolved_artifact_key,
        parent_version_id=resolved_parent_version_id,
        canonical_inputs_version=resolved_inputs,
        files_manifest=[
            {
                "path": summary_file_name,
                "sha256": hashlib.sha256(summary_bytes).hexdigest(),
                "size_bytes": len(summary_bytes),
                "content_type": "application/json",
            }
        ],
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        lifecycle_status=lifecycle_status,
        validation_status=ArtifactValidationStatus.SKIPPED,
        commit_metadata={
            "message": message or f"{source_workflow or 'summary'}: {resolved_artifact_kind}",
            "author_user_id": author_user_id,
            "source_workflow": source_workflow,
            "source_chat_id": source_chat_id,
            "metadata": {
                "summary_payload": summary_payload,
                "summary_format": "json",
            },
        },
    )


__all__ = [
    "extract_summary_payload",
    "persist_summary_artifact",
    "resolve_latest_artifact_version_refs",
]
