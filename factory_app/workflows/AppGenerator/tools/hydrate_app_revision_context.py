"""Load the selected revision baseline before AppGenerator reasons or assembles."""

from __future__ import annotations

import json
from typing import Any

from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from factory_app.workflows._shared.platform.build_target import require_build_binding
from mozaiksai.core.artifacts.models import BuildRecordStatus
from mozaiksai.core.artifacts.store import get_artifact_store
from mozaiksai.core.workflow.context.frozen import detach


async def hydrate_app_revision_context(context_variables: Any = None) -> dict[str, Any]:
    if context_variables is None or context_variables.get("build_mode") != "revision":
        return {"status": "skipped", "reason": "not_revision"}
    if context_variables.get("workflow_sequence") in {"conceptual_replan", "full_rebuild"}:
        return {"status": "skipped", "reason": "explicit_rebuild"}

    binding = require_build_binding(context_variables)
    artifact_id = context_variables.get("artifact_version_id")
    if binding.phase != "refinement" or not artifact_id:
        raise ValueError("revision_baseline_binding_missing")
    artifact = await get_artifact_store().get_build_record(
        app_id=binding.target_app_id, build_record_id=artifact_id,
    )
    if artifact is None or artifact.app_id != binding.target_app_id or artifact.id != artifact_id:
        raise ValueError("revision_baseline_artifact_unavailable")
    if artifact.lifecycle_status in {BuildRecordStatus.ARCHIVED, BuildRecordStatus.DELETED}:
        raise ValueError("revision_baseline_artifact_retired")
    metadata = artifact.commit_metadata.metadata
    if (
        not context_variables.get("user_id")
        or artifact.commit_metadata.author_user_id != context_variables.get("user_id")
        or metadata.get("target_app_id") != binding.target_app_id
        or metadata.get("build_registry_id") != binding.build_registry_id
    ):
        raise ValueError("revision_baseline_owner_mismatch")

    # The selected version can be stale after refinement invalidation. Its
    # committed archive remains the baseline; mutable workspace copies do not.
    files, diagnostics = await read_artifact_bundle(artifact)
    if diagnostics:
        raise ValueError("revision_baseline_incomplete: " + ", ".join(
            str(item["code"]) for item in diagnostics
        ))
    if json.loads(files.get("app.json", "{}")).get("appId") != binding.target_app_id:
        raise ValueError("revision_baseline_app_identity_mismatch")
    current = detach(context_variables.get("generated_files")) or {}
    if not isinstance(current, dict):
        raise ValueError("revision_baseline_generated_files_invalid")
    files.update(current)
    for path in detach(context_variables.get("deleted_files")) or []:
        files.pop(path, None)
    if isinstance(context_variables, dict):
        context_variables["generated_files"] = files
    else:
        context_variables.set("generated_files", files)
    return {"status": "hydrated", "artifact_version_id": artifact_id, "file_count": len(files)}
