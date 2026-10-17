"""Load the selected revision baseline before AppGenerator reasons or assembles."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from factory_app.workflows._shared.platform.build_target import require_build_binding
from factory_app.workflows._shared.platform.genesis_import import require_accepted_genesis_baseline
from mozaiksai.core.artifacts.models import BuildRecordStatus
from mozaiksai.core.artifacts.store import get_artifact_store
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    extract_deleted_file_paths_from_payload,
)


def revision_baseline_required(context_variables: Any) -> bool:
    return bool(
        context_variables is not None
        and context_variables.get("build_mode") == "revision"
        and context_variables.get("workflow_sequence") not in {"conceptual_replan", "full_rebuild"}
    )


def revision_source_artifact_id(context_variables: Any) -> str | None:
    """Keep the selected source stable after artifact_version_id becomes the result."""
    source = context_variables.get("revision_source_artifact_version_id") or context_variables.get("artifact_version_id")
    return source if isinstance(source, str) and source else None


async def read_bound_revision_files(
    context_variables: Any, *, include_binary: bool = False,
) -> dict[str, str | bytes]:
    """Recheck the selected immutable source and its owner on every read."""
    binding = require_build_binding(context_variables)
    artifact_id = revision_source_artifact_id(context_variables)
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

    await require_accepted_genesis_baseline(
        artifact, owner_user_id=context_variables.get("user_id"),
        execution_app_id=context_variables.get("app_id"),
        build_registry_id=binding.build_registry_id,
    )

    # The selected version can be stale after refinement invalidation. Its
    # committed archive remains the baseline; mutable workspace copies do not.
    files, diagnostics = await read_artifact_bundle(artifact, include_binary=include_binary)
    blocking = [item for item in diagnostics if item["blocking"]]
    if blocking:
        raise ValueError("revision_baseline_incomplete: " + ", ".join(
            str(item["code"]) for item in blocking
        ))
    manifest = files.get("app.json")
    if not isinstance(manifest, str) or json.loads(manifest).get("appId") != binding.target_app_id:
        raise ValueError("revision_baseline_app_identity_mismatch")
    return files


async def read_bound_revision_binary_assets(context_variables: Any) -> dict[str, bytes]:
    files = await read_bound_revision_files(context_variables, include_binary=True)
    return {path: content for path, content in files.items() if isinstance(content, bytes)}


async def revision_asset_evidence(
    context_variables: Any, files: dict[str, str],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Describe the exact retained source bytes and current tombstones."""
    source_id = revision_source_artifact_id(context_variables)
    assets = await read_bound_revision_binary_assets(context_variables)
    if revision_source_artifact_id(context_variables) != source_id:
        raise ValueError("revision source changed while assets were read")
    conflicts = set(assets) & set(files)
    if conflicts:
        raise ValueError("revision_binary_text_replacement: " + ", ".join(sorted(conflicts)))
    deleted = set(extract_deleted_file_paths_from_payload({
        "deleted_files": detach(context_variables.get("deleted_files")),
    }))
    # The existing task inventory has no binary deletion grant. A free-form
    # deleted_files entry must not remove an opaque source asset.
    unowned = set(assets) & deleted
    if unowned:
        raise ValueError("revision_binary_deletion_unowned: " + ", ".join(sorted(unowned)))
    evidence = {
        "source_artifact_version_id": source_id,
        "opaque_assets": [
            {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
            for path, data in sorted(assets.items())
        ],
        "deleted_files": sorted(deleted),
    }
    return assets, evidence


async def hydrate_app_revision_context(context_variables: Any = None) -> dict[str, Any]:
    if context_variables is None or context_variables.get("build_mode") != "revision":
        return {"status": "skipped", "reason": "not_revision"}
    if not revision_baseline_required(context_variables):
        return {"status": "skipped", "reason": "explicit_rebuild"}

    files = await read_bound_revision_files(context_variables)
    artifact_id = revision_source_artifact_id(context_variables)
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
