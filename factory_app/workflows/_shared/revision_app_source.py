"""Resolve the selected, owner-bound source archive for Factory revision workflows."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from factory_app.workflows._shared.platform.build_target import require_build_binding
from factory_app.workflows._shared.platform.genesis_import import require_accepted_genesis_baseline
from mozaiksai.control_plane.contracts import (
    BUILD_ACCEPTANCE_TRANSITIONS,
    acceptance_evidence_names,
)
from mozaiksai.core.artifacts.models import (
    BuildRecord,
    BuildRecordStatus,
    BuildRecordValidationStatus,
)
from mozaiksai.core.artifacts.store import get_artifact_store


def revision_is_first_generation(context_variables: Any) -> bool:
    """Only the baseline owner can establish this exception for a design revision."""
    binding = context_variables.get("run_build_binding")
    return bool(
        context_variables.get("build_mode") == "revision"
        and context_variables.get("revision_first_generation") is True
        and context_variables.get("workflow_sequence") == "design_revision"
        and context_variables.get("artifact_kind") == "design_docs"
        and not context_variables.get("revision_source_artifact_version_id")
        and isinstance(binding, Mapping) and binding.get("phase") == "refinement"
    )


def revision_baseline_required(context_variables: Any) -> bool:
    return bool(
        context_variables is not None
        and context_variables.get("build_mode") == "revision"
        and context_variables.get("workflow_sequence") not in {"conceptual_replan", "full_rebuild"}
        and not revision_is_first_generation(context_variables)
    )


def _requested_app_source_id(request: Any) -> str | None:
    if not isinstance(request, Mapping) or request.get("build_family") != "app_bundle":
        return None
    source_id = request.get("build_record_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("revision_baseline_binding_missing")
    return source_id


def revision_source_artifact_id(context_variables: Any) -> str | None:
    """Keep the selected source stable after artifact_version_id becomes the result."""
    if context_variables.get("build_mode") != "revision":
        return None
    if revision_is_first_generation(context_variables):
        return None
    source = context_variables.get("revision_source_artifact_version_id")
    if not source and context_variables.get("workflow_sequence") in {"conceptual_replan", "full_rebuild"}:
        # Rebuilds skip app hydration. Their journey ID advances on each upstream export.
        source = _requested_app_source_id(context_variables.get("refinement_request_meta"))
    source = source or context_variables.get("artifact_version_id")
    return source if isinstance(source, str) and source else None


async def read_bound_revision_files(
    context_variables: Any, *, include_binary: bool = False,
) -> dict[str, str | bytes]:
    """Recheck the selected immutable source and its owner on every read."""
    artifact_id = revision_source_artifact_id(context_variables)
    return await read_revision_files(context_variables, artifact_id, include_binary=include_binary)


def _require_owned_record(context_variables: Any, artifact: BuildRecord | None, artifact_id: str) -> BuildRecord:
    binding = require_build_binding(context_variables)
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
    return artifact


def _require_design_revision_baseline(artifact: BuildRecord) -> None:
    if (
        artifact.build_family != "app_bundle" or artifact.build_key != "app_bundle"
        or artifact.validation_status != BuildRecordValidationStatus.PASSED
        or artifact.lifecycle_status not in {BuildRecordStatus.CURRENT, BuildRecordStatus.STALE}
    ):
        raise ValueError("revision_baseline_not_accepted")
    metadata = artifact.commit_metadata.metadata
    if (
        artifact.lifecycle_status == BuildRecordStatus.CURRENT
        or metadata.get("bundle_mode") == "brownfield_genesis_import"
    ):
        # Imported sources still require their canonical registry receipt below.
        return
    # Invalidation can also mark an unaccepted draft stale. Only the existing
    # acceptance marker establishes that an ordinary stale record was accepted.
    evidence = metadata.get("acceptance")
    if not isinstance(evidence, dict) or not acceptance_evidence_names(artifact, evidence):
        raise ValueError("revision_baseline_not_accepted")
    principal = evidence.get("accepted_by")
    transition = evidence.get("transition")
    accepted_at = evidence.get("accepted_at")
    if isinstance(accepted_at, str):
        try:
            accepted_at = datetime.fromisoformat(accepted_at)
        except ValueError:
            accepted_at = None
    if (
        not isinstance(principal, str) or not principal.strip()
        or not isinstance(transition, str) or transition not in BUILD_ACCEPTANCE_TRANSITIONS
        or not isinstance(accepted_at, datetime)
    ):
        raise ValueError("revision_baseline_not_accepted")


async def read_revision_files(
    context_variables: Any, artifact_id: str | None, *, include_binary: bool = False, require_accepted: bool = False,
) -> dict[str, str | bytes]:
    _, files = await _read_revision_source(
        context_variables, artifact_id, include_binary=include_binary, require_accepted=require_accepted,
    )
    return files


async def _read_revision_source(
    context_variables: Any, artifact_id: str | None, *, include_binary: bool = False, require_accepted: bool = False,
) -> tuple[BuildRecord, dict[str, str | bytes]]:
    binding = require_build_binding(context_variables)
    if binding.phase != "refinement" or not artifact_id:
        raise ValueError("revision_baseline_binding_missing")
    artifact = await get_artifact_store().get_build_record(
        app_id=binding.target_app_id, build_record_id=artifact_id,
    )
    artifact = _require_owned_record(context_variables, artifact, artifact_id)
    if require_accepted or context_variables.get("workflow_sequence") == "design_revision":
        _require_design_revision_baseline(artifact)

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
    return artifact, files


async def design_revision_source(context_variables: Any) -> str | None:
    """Select an implementation independently of the upstream design being revised."""
    binding = require_build_binding(context_variables)
    request = context_variables.get("refinement_request_meta")
    if (
        binding.phase != "refinement"
        or context_variables.get("artifact_kind") != "design_docs"
        or not isinstance(request, Mapping)
        or request.get("build_family") != "design_docs"
        or not isinstance(request.get("build_record_id"), str)
        or not request["build_record_id"].strip()
    ):
        raise ValueError("revision_design_source_binding_missing")
    store = get_artifact_store()
    source_id = request["build_record_id"]
    source = _require_owned_record(context_variables, await store.get_build_record(
        app_id=binding.target_app_id, build_record_id=source_id,
    ), source_id)
    if source.build_family != "design_docs" or source.build_key != "design_docs" or source.source_workflow != "DesignDocs":
        raise ValueError("revision_design_source_invalid")
    pinned = context_variables.get("revision_source_artifact_version_id")
    if pinned:
        if not isinstance(pinned, str):
            raise ValueError("revision_baseline_binding_missing")
        return pinned
    # Do not filter by acceptance/lifecycle: a failed or retired bundle is not
    # evidence that this app has never had an implementation. Store failures
    # propagate; an unavailable lookup must not authorize fresh generation.
    candidates = await store.list_build_records(
        app_id=binding.target_app_id, build_family="app_bundle", limit=1,
    )
    if not isinstance(candidates, list):
        raise ValueError("revision_baseline_lookup_invalid")
    if not candidates:
        return None
    candidate = _require_owned_record(context_variables, candidates[0], candidates[0].id)
    _require_design_revision_baseline(candidate)
    return candidate.id


async def read_accepted_design_app_files(context_variables: Any) -> tuple[BuildRecord, dict[str, str | bytes]] | None:
    """Read the original accepted implementation associated with the design being refined."""
    if context_variables is None or context_variables.get("build_mode") != "revision":
        return None
    request = context_variables.get("refinement_request_meta")
    if isinstance(request, Mapping) and request.get("build_family") == "design_docs":
        source_id = await design_revision_source(context_variables)
    else:
        source_id = _requested_app_source_id(request) or context_variables.get("revision_source_artifact_version_id")
    if not source_id:
        return None
    return await _read_revision_source(context_variables, source_id, require_accepted=True)
