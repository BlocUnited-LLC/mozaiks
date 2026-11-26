"""Load the selected revision baseline before AppGenerator reasons or assembles."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from factory_app.workflows._shared.revision_app_source import (
    design_revision_source,
    is_accepted_revision_source,
    read_bound_revision_files,
    read_revision_source,
    revision_baseline_required,
    revision_source_artifact_id,
)
from mozaiksai.core.runtime.app.theme_contract import APP_THEME_PATH
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    extract_deleted_file_paths_from_payload,
)
from mozaiksai.core.workflow.generator_support.module_revision import (
    legacy_optional_updated_at_reads,
)


def _set(context_variables: Any, key: str, value: Any) -> None:
    if isinstance(context_variables, dict):
        context_variables[key] = value
    else:
        context_variables.set(key, value)


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
    """Seed the agent-visible theme under the before_chat lifecycle writer."""
    return await hydrate_revision_files(context_variables, initialize_theme=True)


def uses_revision_source_theme(context_variables: Any) -> bool:
    return (
        revision_baseline_required(context_variables)
        and context_variables.get("artifact_kind") == "app_bundle"
        and context_variables.get("workflow_sequence") not in {"theme_revision", "theme_patch"}
    )


def read_revision_theme(files: dict[str, Any]) -> dict[str, Any] | None:
    theme = json.loads(files[APP_THEME_PATH]) if APP_THEME_PATH in files else None
    if theme is not None and not isinstance(theme, dict):
        raise ValueError("revision_baseline_theme_invalid")
    return theme


async def hydrate_revision_files(context_variables: Any, *, initialize_theme: bool = False) -> dict[str, Any]:
    if context_variables is None:
        return {"status": "skipped", "reason": "not_revision"}
    _set(context_variables, "legacy_optional_updated_at_reads", {})
    if context_variables.get("build_mode") != "revision":
        return {"status": "skipped", "reason": "not_revision"}
    # Recompute even after replay. A carried flag cannot waive source/owner or
    # existence checks, and a failed recheck leaves the baseline required.
    _set(context_variables, "revision_first_generation", False)
    if not revision_baseline_required(context_variables):
        return {"status": "skipped", "reason": "explicit_rebuild"}

    if context_variables.get("workflow_sequence") == "design_revision":
        artifact_id = await design_revision_source(context_variables)
        if artifact_id is None:
            _set(context_variables, "revision_first_generation", True)
            return {"status": "skipped", "reason": "first_generation"}
    else:
        artifact_id = revision_source_artifact_id(context_variables)
    artifact, files = await read_revision_source(context_variables, artifact_id)
    _set(context_variables, "revision_source_artifact_version_id", artifact.id)
    if initialize_theme and uses_revision_source_theme(context_variables):
        _set(context_variables, "captured_theme_config", read_revision_theme(files))
    if is_accepted_revision_source(artifact):
        reads = legacy_optional_updated_at_reads(files, context_variables.get("data_contract"))
        _set(context_variables, "legacy_optional_updated_at_reads", reads)
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
