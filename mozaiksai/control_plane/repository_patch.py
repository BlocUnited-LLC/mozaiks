"""Reviewable repository patches from an approved, harvested coding workspace.

The authenticated host owns plan approval, snapshot identity, repository reads,
and source-control publication. This module only verifies one bounded, already
staged coding result and returns an external-patch candidate for later review.
"""

from __future__ import annotations

import difflib
import hashlib
from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contracts import StagedPatchProposal, is_secret_sensitive_path, safe_artifact_relpath
from .execution_context import ApprovedExecutionContext
from .workspace import StagedCodingWorkspace, harvest_coding_workspace


class RepositorySnapshotEvidence(BaseModel):
    """Host-verified projection of the persisted approved repository snapshot.

    The host must verify that this is the complete immutable snapshot record
    bound to the approved plan. Its digest cannot be recomputed here because
    hosted snapshot identity includes host-owned tenant and exclusion evidence.
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    repository_full_name: str = Field(min_length=1)
    baseline_commit_sha: str = Field(min_length=40, max_length=40)
    snapshot_digest: str = Field(min_length=1)
    file_manifest: dict[str, str] = Field(min_length=1)

    @field_validator("file_manifest")
    @classmethod
    def _validate_manifest(cls, manifest: dict[str, str]) -> dict[str, str]:
        for path, digest in manifest.items():
            _canonical_path(path)
            if not digest.startswith("sha256:") or len(digest) != 71 or any(
                char not in "0123456789abcdef" for char in digest[7:]
            ):
                raise ValueError(f"REPOSITORY_PATCH_MANIFEST_HASH: {path!r}")
        return manifest


class RepositoryPatchFile(BaseModel):
    """One update, with bytes independently observed in the staged workspace."""

    model_config = ConfigDict(extra="forbid")

    path: str
    op: Literal["update"] = "update"
    previous_sha256: str
    new_sha256: str
    content: str
    diff: str


class RepositoryPatchCandidate(BaseModel):
    """Unvalidated external patch for host-owned review and PR publication."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.refinement.repository_patch.v1"] = (
        "mozaiks.refinement.repository_patch.v1"
    )
    write_back_mode: Literal["external_patch"] = "external_patch"
    validation_state: Literal["pending"] = "pending"
    mutation_allowed: Literal[False] = False
    handoff_id: str
    request_id: str
    plan_id: str
    app_id: str
    build_registry_id: str
    repository_full_name: str
    baseline_commit_sha: str
    approved_plan_digest: str
    snapshot_digest: str
    proposal_id: str
    provider_id: str
    summary: str
    rationale: str
    changed_files: list[RepositoryPatchFile]
    required_validation_gates: list[str]
    required_security_gates: list[str]
    required_ci_checks: list[str]


def _canonical_path(path: str) -> str:
    normalized = safe_artifact_relpath(path)
    if normalized is None or normalized == "." or normalized != path:
        raise ValueError(f"REPOSITORY_PATCH_UNSAFE_PATH: {path!r}")
    return normalized


def _in_scope(path: str, paths: list[str]) -> bool:
    return any(path == scope or path.startswith(f"{scope.rstrip('/')}/") for scope in paths)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _unified_diff(path: str, before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="\n",
    )
    # difflib does not add Git's missing-final-newline marker to content lines.
    return "".join(
        line if line.endswith("\n") else f"{line}\n\\ No newline at end of file\n"
        for line in lines
    )


def finalize_repository_patch(
    context: ApprovedExecutionContext,
    *,
    snapshot: RepositorySnapshotEvidence,
    selected_paths: list[str],
    baseline_files: Mapping[str, str],
    workspace: StagedCodingWorkspace,
    proposal: StagedPatchProposal,
    validate_path: Callable[[str], object],
) -> RepositoryPatchCandidate:
    """Verify an approved repository edit against an independently harvested tree.

    This function reads the staged workspace and performs no writes, validation
    commands, Git actions, artifact promotion, or model calls. The host must
    supply a verified snapshot, fetch baseline files at its immutable commit,
    keep the provider confined to a disposable workspace, and call this only
    after the provider has stopped modifying that workspace.

    ``validate_path`` is the host's per-file policy. It must raise for any
    denied path; the finalizer calls it for every selected and proposed path.
    """

    if context.schema_version != "managed_refinement.execution_context.v1":
        raise ValueError("REPOSITORY_PATCH_CONTEXT_VERSION: unsupported execution context")
    if (
        snapshot.plan_id != context.plan_id
        or snapshot.request_id != context.request_id
        or snapshot.app_id != context.app_id
        or snapshot.repository_full_name != context.repository_full_name
        or snapshot.baseline_commit_sha != context.baseline_commit_sha
        or snapshot.snapshot_digest != context.snapshot_digest
    ):
        raise ValueError("REPOSITORY_PATCH_SNAPSHOT_IDENTITY: snapshot does not match approval")
    if not selected_paths or len(set(selected_paths)) != len(selected_paths):
        raise ValueError("REPOSITORY_PATCH_SELECTION: selected paths must be unique and nonempty")
    for path in baseline_files:
        _canonical_path(path)
        if validate_path(path) is not None:
            raise ValueError("REPOSITORY_PATCH_HOST_POLICY: validate_path must raise or return None")
    if set(baseline_files) != set(selected_paths):
        raise ValueError("REPOSITORY_PATCH_BASELINE_SET: baseline files must match selected paths")

    expected_hashes: dict[str, str] = {}
    for path in selected_paths:
        _canonical_path(path)
        if is_secret_sensitive_path(path):
            raise ValueError(f"REPOSITORY_PATCH_SECRET_PATH: {path!r}")
        if (
            not _in_scope(path, context.allowed_paths)
            or _in_scope(path, context.prohibited_paths)
            or _in_scope(path, context.read_only_paths)
        ):
            raise ValueError(f"REPOSITORY_PATCH_SCOPE: {path!r} is not editable")
        if validate_path(path) is not None:
            raise ValueError("REPOSITORY_PATCH_HOST_POLICY: validate_path must raise or return None")
        manifest_hash = snapshot.file_manifest.get(path)
        content_hash = _sha256(baseline_files[path])
        if manifest_hash != f"sha256:{content_hash}":
            raise ValueError(f"REPOSITORY_PATCH_BASELINE_HASH: {path!r} differs from approved snapshot")
        expected_hashes[path] = content_hash

    if workspace.editable_manifest != expected_hashes:
        raise ValueError("REPOSITORY_PATCH_WORKSPACE_BASELINE: staged files differ from selected baseline")
    harvest = harvest_coding_workspace(workspace)
    observed_paths = [file.path for file in harvest.files]
    observed_paths.extend(violation.path for violation in harvest.violations)
    for path in observed_paths:
        _canonical_path(path)
        if validate_path(path) is not None:
            raise ValueError("REPOSITORY_PATCH_HOST_POLICY: validate_path must raise or return None")
    if harvest.violations:
        violations = ", ".join(f"{item.kind}:{item.path}" for item in harvest.violations)
        raise ValueError(f"REPOSITORY_PATCH_WORKSPACE_SCOPE: {violations}")
    if {file.path for file in harvest.files} != set(selected_paths):
        raise ValueError("REPOSITORY_PATCH_WORKSPACE_SET: harvested files differ from selection")

    harvested_changes: dict[str, str] = {}
    for file in harvest.files:
        if file.op != "update" or file.content is None:
            raise ValueError(f"REPOSITORY_PATCH_WORKSPACE_OP: {file.path!r}")
        observed_hash = _sha256(file.content)
        if (
            file.previous_sha256 != expected_hashes[file.path]
            or file.new_sha256 != observed_hash
            or file.modified != (observed_hash != expected_hashes[file.path])
        ):
            raise ValueError(f"REPOSITORY_PATCH_WORKSPACE_HASH: {file.path!r}")
        if file.modified:
            harvested_changes[file.path] = file.content

    if proposal.status != "completed" or not harvested_changes:
        raise ValueError("REPOSITORY_PATCH_PROPOSAL_STATUS: completed nonempty edit required")
    proposed: dict[str, str] = {}
    for change in proposal.changed_files:
        path = _canonical_path(change.path)
        if validate_path(path) is not None:
            raise ValueError("REPOSITORY_PATCH_HOST_POLICY: validate_path must raise or return None")
        if change.op != "update" or path not in expected_hashes or path in proposed:
            raise ValueError(f"REPOSITORY_PATCH_PROPOSAL_SCOPE: {path!r}")
        proposed[path] = change.content
    if proposed != harvested_changes:
        raise ValueError("REPOSITORY_PATCH_PROPOSAL_MISMATCH: provider output differs from staged bytes")
    owned_paths = [_canonical_path(path) for path in proposal.owned_paths]
    if len(set(owned_paths)) != len(owned_paths) or not set(owned_paths) <= set(selected_paths):
        raise ValueError("REPOSITORY_PATCH_OWNERSHIP: provider claimed an unselected path")
    if not set(proposed) <= set(owned_paths):
        raise ValueError("REPOSITORY_PATCH_OWNERSHIP: changed paths were not claimed")

    changes = [
        RepositoryPatchFile(
            path=path,
            previous_sha256=f"sha256:{expected_hashes[path]}",
            new_sha256=f"sha256:{_sha256(harvested_changes[path])}",
            content=harvested_changes[path],
            diff=_unified_diff(path, baseline_files[path], harvested_changes[path]),
        )
        for path in sorted(harvested_changes)
    ]
    return RepositoryPatchCandidate(
        handoff_id=context.handoff_id,
        request_id=context.request_id,
        plan_id=context.plan_id,
        app_id=context.app_id,
        build_registry_id=context.build_registry_id,
        repository_full_name=context.repository_full_name,
        baseline_commit_sha=context.baseline_commit_sha,
        approved_plan_digest=context.approved_plan_digest,
        snapshot_digest=context.snapshot_digest,
        proposal_id=proposal.proposal_id,
        provider_id=proposal.provider_id,
        summary=proposal.summary,
        rationale=proposal.rationale,
        changed_files=changes,
        required_validation_gates=context.required_validation_gates,
        required_security_gates=context.required_security_gates,
        required_ci_checks=context.required_ci_checks,
    )


__all__ = [
    "RepositoryPatchCandidate",
    "RepositoryPatchFile",
    "RepositorySnapshotEvidence",
    "finalize_repository_patch",
]
