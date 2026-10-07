"""Repository patch candidates are based on observed staged bytes, not provider claims."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from mozaiksai.control_plane import (
    ApprovedExecutionContext,
    ProposedFileChange,
    RepositorySnapshotEvidence,
    StagedPatchProposal,
    finalize_repository_patch,
)
from mozaiksai.control_plane.workspace import materialize_coding_workspace

PATH = "app/modules/support/backend/service.py"
OTHER_PATH = "app/modules/support/backend/schemas.py"
BEFORE = "def answer():\n    return 1\n"
AFTER = "def answer():\n    return 2\n"


def _sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _context(**overrides: object) -> ApprovedExecutionContext:
    values: dict[str, object] = {
        "handoff_id": "handoff-1",
        "request_id": "request-1",
        "plan_id": "plan-1",
        "app_id": "app-zero",
        "build_registry_id": "registry-1",
        "repository_full_name": "BlocUnited-LLC/mozaiks-app",
        "baseline_commit_sha": "a" * 40,
        "raw_request": "Fix the support answer",
        "request_type": "patch",
        "approved_plan_digest": "b" * 64,
        "snapshot_digest": "sha256:" + "c" * 64,
        "graph_identity_digest": "graph-1",
        "impact_report_digest": "impact-1",
        "execution_strategy_id": "standard_coding_refinement",
        "rationale": "A small code change.",
        "allowed_paths": ["app/modules/support/"],
        "required_validation_gates": ["pytest"],
        "required_security_gates": ["secret_scan"],
        "required_ci_checks": ["lint"],
    }
    values.update(overrides)
    return ApprovedExecutionContext.model_validate(values)


def _proposal(**overrides: object) -> StagedPatchProposal:
    values: dict[str, object] = {
        "proposal_id": "proposal-1",
        "provider_id": "fixture",
        "status": "completed",
        "summary": "Fix support answer",
        "rationale": "The return value was wrong.",
        "owned_paths": [PATH],
        "changed_files": [ProposedFileChange(path=PATH, op="update", content=AFTER)],
    }
    values.update(overrides)
    return StagedPatchProposal.model_validate(values)


def _inputs(tmp_path: Path):
    context = _context()
    baseline_files = {PATH: BEFORE, OTHER_PATH: "class Answer: pass\n"}
    snapshot = RepositorySnapshotEvidence(
        plan_id=context.plan_id,
        request_id=context.request_id,
        app_id=context.app_id,
        repository_full_name=context.repository_full_name,
        baseline_commit_sha=context.baseline_commit_sha,
        snapshot_digest=context.snapshot_digest,
        file_manifest={path: _sha256(content) for path, content in baseline_files.items()},
    )
    workspace = materialize_coding_workspace(baseline_files, workspace_root=tmp_path / "staged")
    (workspace.workspace_root / PATH).write_bytes(AFTER.encode("utf-8"))
    return {
        "context": context,
        "snapshot": snapshot,
        "selected_paths": [PATH, OTHER_PATH],
        "baseline_files": baseline_files,
        "workspace": workspace,
        "proposal": _proposal(),
        "validate_path": Mock(return_value=None),
    }


def test_finalizes_observed_update_as_unvalidated_external_patch(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    staged_path = inputs["workspace"].workspace_root / PATH
    before_finalization = staged_path.read_bytes()

    candidate = finalize_repository_patch(**inputs)

    assert candidate.schema_version == "mozaiks.refinement.repository_patch.v1"
    assert candidate.write_back_mode == "external_patch"
    assert candidate.validation_state == "pending"
    assert candidate.mutation_allowed is False
    assert candidate.baseline_commit_sha == "a" * 40
    assert candidate.build_registry_id == "registry-1"
    assert candidate.required_validation_gates == ["pytest"]
    assert candidate.required_security_gates == ["secret_scan"]
    assert candidate.required_ci_checks == ["lint"]
    assert len(candidate.changed_files) == 1
    change = candidate.changed_files[0]
    assert change.path == PATH
    assert change.previous_sha256 == _sha256(BEFORE)
    assert change.new_sha256 == _sha256(AFTER)
    assert change.content == AFTER
    assert f"--- a/{PATH}\n+++ b/{PATH}\n" in change.diff
    assert "-    return 1\n+    return 2\n" in change.diff
    assert staged_path.read_bytes() == before_finalization
    assert inputs["validate_path"].call_count == 7  # baseline, selected, harvested, output


@pytest.mark.parametrize(
    ("snapshot_update", "expected"),
    [
        ({"plan_id": "plan-other"}, "SNAPSHOT_IDENTITY"),
        ({"request_id": "request-other"}, "SNAPSHOT_IDENTITY"),
        ({"app_id": "app-other"}, "SNAPSHOT_IDENTITY"),
        ({"baseline_commit_sha": "d" * 40}, "SNAPSHOT_IDENTITY"),
        ({"snapshot_digest": "sha256:" + "d" * 64}, "SNAPSHOT_IDENTITY"),
        ({"repository_full_name": "other/repo"}, "SNAPSHOT_IDENTITY"),
        ({"file_manifest": {PATH: _sha256("stale"), OTHER_PATH: _sha256("class Answer: pass\n")}}, "BASELINE_HASH"),
    ],
)
def test_rejects_stale_or_unbound_snapshot(
    tmp_path: Path, snapshot_update: dict[str, object], expected: str
) -> None:
    inputs = _inputs(tmp_path)
    inputs["snapshot"] = inputs["snapshot"].model_copy(update=snapshot_update)
    with pytest.raises(ValueError, match=expected):
        finalize_repository_patch(**inputs)


@pytest.mark.parametrize(
    "manifest",
    [
        {"../outside.py": _sha256(BEFORE)},
        {PATH: "sha256:invalid"},
    ],
)
def test_snapshot_manifest_requires_canonical_paths_and_content_hashes(manifest: dict[str, str]) -> None:
    context = _context()
    with pytest.raises(ValueError):
        RepositorySnapshotEvidence(
            plan_id=context.plan_id,
            request_id=context.request_id,
            app_id=context.app_id,
            repository_full_name=context.repository_full_name,
            baseline_commit_sha=context.baseline_commit_sha,
            snapshot_digest=context.snapshot_digest,
            file_manifest=manifest,
        )


@pytest.mark.parametrize(
    ("context_update", "expected"),
    [
        ({"allowed_paths": ["app/"], "prohibited_paths": [PATH]}, "SCOPE"),
        ({"allowed_paths": ["app/"], "read_only_paths": ["app/modules/support/"]}, "SCOPE"),
        ({"allowed_paths": ["app/other/"]}, "SCOPE"),
        ({"schema_version": "managed_refinement.execution_context.v2"}, "CONTEXT_VERSION"),
    ],
)
def test_approval_scope_and_version_are_enforced(
    tmp_path: Path, context_update: dict[str, object], expected: str
) -> None:
    inputs = _inputs(tmp_path)
    inputs["context"] = inputs["context"].model_copy(update=context_update)
    with pytest.raises(ValueError, match=expected):
        finalize_repository_patch(**inputs)


def test_host_path_policy_rejects_protected_descendant_of_broad_scope(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    protected = "app/data/contract.json"
    inputs["context"] = _context(allowed_paths=["app/"])
    inputs["selected_paths"] = [protected]
    inputs["baseline_files"] = {protected: "{}\n"}
    inputs["snapshot"] = inputs["snapshot"].model_copy(
        update={"file_manifest": {protected: _sha256("{}\n")}}
    )

    def host_policy(path: str) -> None:
        if path == protected:
            raise ValueError("protected host authority")

    inputs["validate_path"] = host_policy
    with pytest.raises(ValueError, match="protected host authority"):
        finalize_repository_patch(**inputs)


@pytest.mark.parametrize("selected", [[PATH, PATH], [PATH], [PATH, "../outside.py"]])
def test_selected_paths_must_be_unique_exact_baseline_and_safe(tmp_path: Path, selected: list[str]) -> None:
    inputs = _inputs(tmp_path)
    inputs["selected_paths"] = selected
    with pytest.raises(ValueError, match="SELECTION|BASELINE_SET|UNSAFE_PATH"):
        finalize_repository_patch(**inputs)


def test_workspace_initial_manifest_must_match_approved_baseline(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    inputs["workspace"].editable_manifest[PATH] = "d" * 64
    with pytest.raises(ValueError, match="WORKSPACE_BASELINE"):
        finalize_repository_patch(**inputs)


@pytest.mark.parametrize("extra", ["app/new.py", "app/modules/support/new.py"])
def test_unselected_workspace_file_is_rejected(tmp_path: Path, extra: str) -> None:
    inputs = _inputs(tmp_path)
    # The tree walk reports a created file regardless of the provider's claims.
    path = inputs["workspace"].workspace_root / extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("surprise\n", encoding="utf-8")
    with pytest.raises(ValueError, match="WORKSPACE_SCOPE"):
        finalize_repository_patch(**inputs)


def test_deleted_workspace_file_is_rejected(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    (inputs["workspace"].workspace_root / OTHER_PATH).unlink()
    with pytest.raises(ValueError, match="delete_denied"):
        finalize_repository_patch(**inputs)


def test_symlink_in_workspace_is_rejected(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    target = inputs["workspace"].workspace_root / PATH
    target.unlink()
    try:
        target.symlink_to(tmp_path / "outside.py")
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="symlink"):
        finalize_repository_patch(**inputs)


def test_non_utf8_harvest_cannot_be_represented_as_text_patch(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    (inputs["workspace"].workspace_root / PATH).write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="WORKSPACE_HASH"):
        finalize_repository_patch(**inputs)


@pytest.mark.parametrize(
    ("proposal_update", "expected"),
    [
        ({"status": "failed"}, "PROPOSAL_STATUS"),
        ({"changed_files": [ProposedFileChange(path=PATH, content="forged")]}, "PROPOSAL_MISMATCH"),
        ({"changed_files": [ProposedFileChange(path="app/new.py", op="create", content="x")]}, "PROPOSAL_SCOPE"),
        ({"changed_files": [ProposedFileChange(path=PATH, content=AFTER)] * 2}, "PROPOSAL_SCOPE"),
        ({"changed_files": [ProposedFileChange(path="../outside.py", content="x")]}, "UNSAFE_PATH"),
        ({"owned_paths": ["app"]}, "OWNERSHIP"),
        ({"owned_paths": []}, "OWNERSHIP"),
    ],
)
def test_rejects_forged_or_out_of_scope_provider_claims(
    tmp_path: Path, proposal_update: dict[str, object], expected: str
) -> None:
    inputs = _inputs(tmp_path)
    inputs["proposal"] = inputs["proposal"].model_copy(update=proposal_update)
    with pytest.raises(ValueError, match=expected):
        finalize_repository_patch(**inputs)


def test_no_effective_staged_change_cannot_claim_a_patch(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    (inputs["workspace"].workspace_root / PATH).write_bytes(BEFORE.encode())
    with pytest.raises(ValueError, match="PROPOSAL_STATUS"):
        finalize_repository_patch(**inputs)


def test_missing_final_newline_is_explicit_in_review_diff(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    after = "def answer():\n    return 2"
    (inputs["workspace"].workspace_root / PATH).write_bytes(after.encode())
    inputs["proposal"] = _proposal(changed_files=[ProposedFileChange(path=PATH, content=after)])

    candidate = finalize_repository_patch(**inputs)

    assert "\\ No newline at end of file\n" in candidate.changed_files[0].diff
