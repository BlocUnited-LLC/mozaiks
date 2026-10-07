"""Repository patch candidates are based on observed staged bytes, not provider claims."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from unittest.mock import Mock

import pytest

from mozaiksai.control_plane import (
    ApprovedExecutionContext,
    ProposedFileChange,
    RepositorySnapshotEvidence,
    StagedPatchProposal,
    export_repository_workspace_archive,
    finalize_repository_patch,
    select_repository_read_only_files,
    stage_repository_workspace_archive,
)
from mozaiksai.control_plane.workspace import materialize_coding_workspace
from mozaiksai.core.secrets.contract import SecretContractError
from mozaiksai.core.semantics.archive import (
    ArchiveEntry,
    build_deterministic_archive,
    read_archive_manifest,
)

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


def test_archive_transport_reconstructs_approved_baseline_before_finalization(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    archive = export_repository_workspace_archive(
        inputs["workspace"], max_files=2, max_archive_bytes=4096
    )
    assert {entry.path for entry in read_archive_manifest(archive).entries} == {PATH, OTHER_PATH}
    staged = stage_repository_workspace_archive(
        inputs["context"], snapshot=inputs["snapshot"], selected_paths=inputs["selected_paths"],
        baseline_files=inputs["baseline_files"], archive_bytes=archive,
        workspace_root=tmp_path / "reconstructed", validate_path=inputs["validate_path"],
        max_files=2, max_archive_bytes=4096,
    )
    try:
        assert staged.editable_manifest[PATH] == _sha256(BEFORE)[7:]
        assert (staged.workspace_root / PATH).read_bytes() == AFTER.encode("utf-8")
        candidate = finalize_repository_patch(**{**inputs, "workspace": staged})
        assert candidate.changed_files[0].content == AFTER
        assert candidate.changed_files[0].previous_sha256 == _sha256(BEFORE)
    finally:
        staged.cleanup()
    assert not (tmp_path / "reconstructed").exists()


def test_archive_export_rejects_unselected_or_non_utf8_workspace_bytes(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    rogue = inputs["workspace"].workspace_root / "app/rogue.py"
    rogue.write_text("rogue\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ARCHIVE_SCOPE"):
        export_repository_workspace_archive(inputs["workspace"], max_files=3, max_archive_bytes=4096)
    rogue.unlink()
    (inputs["workspace"].workspace_root / PATH).write_bytes(b"\xff")
    with pytest.raises(ValueError, match="ARCHIVE_UTF8"):
        export_repository_workspace_archive(inputs["workspace"], max_files=3, max_archive_bytes=4096)


def test_archive_transport_does_not_authorize_forged_provider_content(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    archive = export_repository_workspace_archive(inputs["workspace"], max_files=2, max_archive_bytes=4096)
    staged = stage_repository_workspace_archive(
        inputs["context"], snapshot=inputs["snapshot"], selected_paths=inputs["selected_paths"],
        baseline_files=inputs["baseline_files"], archive_bytes=archive,
        workspace_root=tmp_path / "reconstructed", validate_path=inputs["validate_path"],
        max_files=2, max_archive_bytes=4096,
    )
    try:
        forged = _proposal(changed_files=[ProposedFileChange(path=PATH, content="forged\n")])
        with pytest.raises(ValueError, match="PROPOSAL_MISMATCH"):
            finalize_repository_patch(**{**inputs, "workspace": staged, "proposal": forged})
    finally:
        staged.cleanup()


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([(PATH, AFTER.encode()), ("app/rogue.py", b"rogue")], "ARCHIVE_SET"),
        ([(PATH, b"\xff"), (OTHER_PATH, b"class Answer: pass\n")], "ARCHIVE_UTF8"),
    ],
)
def test_archive_import_rejects_unapproved_or_non_utf8_output_before_writing(
    tmp_path: Path, entries: list[tuple[str, bytes]], expected: str,
) -> None:
    inputs = _inputs(tmp_path)
    archive = build_deterministic_archive([ArchiveEntry(path=path, content=raw) for path, raw in entries])
    root = tmp_path / "reconstructed"
    with pytest.raises(ValueError, match=expected):
        stage_repository_workspace_archive(
            inputs["context"], snapshot=inputs["snapshot"], selected_paths=inputs["selected_paths"],
            baseline_files=inputs["baseline_files"], archive_bytes=archive,
            workspace_root=root, validate_path=inputs["validate_path"],
            max_files=2, max_archive_bytes=4096,
        )
    assert not root.exists()


def test_archive_import_enforces_host_policy_and_byte_limit_before_writing(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    archive = export_repository_workspace_archive(inputs["workspace"], max_files=2, max_archive_bytes=4096)
    root = tmp_path / "reconstructed"
    with pytest.raises(ValueError, match="ARCHIVE_BUDGET"):
        export_repository_workspace_archive(inputs["workspace"], max_files=1, max_archive_bytes=4096)

    def deny(path: str) -> None:
        if path == PATH:
            raise ValueError("host denied")

    with pytest.raises(ValueError, match="host denied"):
        stage_repository_workspace_archive(
            inputs["context"], snapshot=inputs["snapshot"], selected_paths=inputs["selected_paths"],
            baseline_files=inputs["baseline_files"], archive_bytes=archive,
            workspace_root=root, validate_path=deny, max_files=2, max_archive_bytes=4096,
        )
    assert not root.exists()
    with pytest.raises(ValueError, match="ARCHIVE_BUDGET"):
        stage_repository_workspace_archive(
            inputs["context"], snapshot=inputs["snapshot"], selected_paths=inputs["selected_paths"],
            baseline_files=inputs["baseline_files"], archive_bytes=archive,
            workspace_root=root, validate_path=lambda path: None,
            max_files=2, max_archive_bytes=100,
        )
    assert not root.exists()
    with pytest.raises(ValueError, match="ARCHIVE_BUDGET"):
        stage_repository_workspace_archive(
            inputs["context"], snapshot=inputs["snapshot"], selected_paths=inputs["selected_paths"],
            baseline_files=inputs["baseline_files"], archive_bytes=archive,
            workspace_root=root, validate_path=lambda path: None,
            max_files=1, max_archive_bytes=4096,
        )
    assert not root.exists()


def test_archive_import_rejects_many_entries_before_zipfile_parses_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    archive = bytearray(build_deterministic_archive([
        ArchiveEntry(path=f"app/file-{number:04}.py", content=b"")
        for number in range(1000)
    ]))
    assert len(archive) < 200_000
    # A forged EOCD count alone must not hide a large central directory.
    struct.pack_into("<HH", archive, len(archive) - 22 + 8, 2, 2)
    root = tmp_path / "reconstructed"
    zip_parser = Mock(side_effect=AssertionError("ZipFile parsed unbounded directory"))
    with monkeypatch.context() as patch:
        patch.setattr("mozaiksai.control_plane.repository_patch.zipfile.ZipFile", zip_parser)
        with pytest.raises(ValueError, match="REPOSITORY_PATCH_ARCHIVE_BUDGET"):
            stage_repository_workspace_archive(
                inputs["context"], snapshot=inputs["snapshot"],
                selected_paths=inputs["selected_paths"], baseline_files=inputs["baseline_files"],
                archive_bytes=bytes(archive), workspace_root=root,
                validate_path=inputs["validate_path"], max_files=2, max_archive_bytes=200_000,
            )
    zip_parser.assert_not_called()
    assert not root.exists()


def test_archive_import_rejects_snapshot_replay_before_writing(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    archive = export_repository_workspace_archive(inputs["workspace"], max_files=2, max_archive_bytes=4096)
    stale = inputs["snapshot"].model_copy(update={"plan_id": "another-plan"})
    root = tmp_path / "reconstructed"
    with pytest.raises(ValueError, match="SNAPSHOT_IDENTITY"):
        stage_repository_workspace_archive(
            inputs["context"], snapshot=stale, selected_paths=inputs["selected_paths"],
            baseline_files=inputs["baseline_files"], archive_bytes=archive,
            workspace_root=root, validate_path=inputs["validate_path"],
            max_files=2, max_archive_bytes=4096,
        )
    assert not root.exists()


def test_archive_import_cleans_partial_baseline_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    archive = export_repository_workspace_archive(inputs["workspace"], max_files=2, max_archive_bytes=4096)
    root = tmp_path / "reconstructed"

    def fail_after_partial_write(files: dict[str, str], *, workspace_root: Path) -> None:
        workspace_root.mkdir(parents=True)
        (workspace_root / "partial.py").write_text("partial", encoding="utf-8")
        raise ValueError("baseline materialization failed")

    monkeypatch.setattr(
        "mozaiksai.control_plane.repository_patch.materialize_coding_workspace",
        fail_after_partial_write,
    )
    with pytest.raises(ValueError, match="baseline materialization failed"):
        stage_repository_workspace_archive(
            inputs["context"], snapshot=inputs["snapshot"], selected_paths=inputs["selected_paths"],
            baseline_files=inputs["baseline_files"], archive_bytes=archive,
            workspace_root=root, validate_path=inputs["validate_path"],
            max_files=2, max_archive_bytes=4096,
        )
    assert not root.exists()


def test_archive_import_reports_failed_cleanup_after_partial_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    archive = export_repository_workspace_archive(inputs["workspace"], max_files=2, max_archive_bytes=4096)
    root = tmp_path / "reconstructed"

    def fail_after_partial_write(files: dict[str, str], *, workspace_root: Path) -> None:
        workspace_root.mkdir(parents=True)
        (workspace_root / "partial.py").write_text("partial", encoding="utf-8")
        raise ValueError("baseline materialization failed")

    def deny_cleanup(*args: object, **kwargs: object) -> None:
        raise PermissionError("locked staging tree")

    with monkeypatch.context() as patch:
        patch.setattr(
            "mozaiksai.control_plane.repository_patch.materialize_coding_workspace",
            fail_after_partial_write,
        )
        patch.setattr("mozaiksai.control_plane.workspace.shutil.rmtree", deny_cleanup)
        with pytest.raises(RuntimeError, match="CODING_WORKSPACE_CLEANUP"):
            stage_repository_workspace_archive(
                inputs["context"], snapshot=inputs["snapshot"],
                selected_paths=inputs["selected_paths"], baseline_files=inputs["baseline_files"],
                archive_bytes=archive, workspace_root=root, validate_path=inputs["validate_path"],
                max_files=2, max_archive_bytes=4096,
            )
    assert (root / "partial.py").exists()


def test_archive_import_reports_failed_cleanup_after_successful_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    archive = export_repository_workspace_archive(inputs["workspace"], max_files=2, max_archive_bytes=4096)
    root = tmp_path / "reconstructed"
    staged = stage_repository_workspace_archive(
        inputs["context"], snapshot=inputs["snapshot"], selected_paths=inputs["selected_paths"],
        baseline_files=inputs["baseline_files"], archive_bytes=archive,
        workspace_root=root, validate_path=inputs["validate_path"],
        max_files=2, max_archive_bytes=4096,
    )

    def deny_cleanup(*args: object, **kwargs: object) -> None:
        raise PermissionError("locked staging tree")

    with monkeypatch.context() as patch:
        patch.setattr("mozaiksai.control_plane.workspace.shutil.rmtree", deny_cleanup)
        with pytest.raises(RuntimeError, match="CODING_WORKSPACE_CLEANUP"):
            staged.cleanup()
    assert root.exists()
    staged.cleanup()
    staged.cleanup()  # Strict host cleanup remains idempotent.
    assert not root.exists()


def test_read_only_snapshot_context_cannot_enter_editable_archive(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    read_only = "tests/test_support.py"
    read_only_content = "def test_answer(): assert True\n"
    inputs["context"] = _context(allowed_paths=["app/", "tests/"], read_only_paths=["tests/"])
    inputs["snapshot"] = inputs["snapshot"].model_copy(update={
        "file_manifest": {
            **inputs["snapshot"].file_manifest,
            read_only: _sha256(read_only_content),
        }
    })
    approved_archive = export_repository_workspace_archive(
        inputs["workspace"], max_files=2, max_archive_bytes=4096
    )
    staged = stage_repository_workspace_archive(
        inputs["context"], snapshot=inputs["snapshot"], selected_paths=inputs["selected_paths"],
        baseline_files=inputs["baseline_files"], archive_bytes=approved_archive,
        workspace_root=tmp_path / "approved", validate_path=inputs["validate_path"],
        max_files=2, max_archive_bytes=4096,
    )
    try:
        candidate = finalize_repository_patch(**{**inputs, "workspace": staged})
        assert [change.path for change in candidate.changed_files] == [PATH]
    finally:
        staged.cleanup()

    poisoned_archive = build_deterministic_archive([
        ArchiveEntry(path=PATH, content=AFTER.encode()),
        ArchiveEntry(path=read_only, content=read_only_content.encode()),
    ])
    root = tmp_path / "poisoned"
    with pytest.raises(ValueError, match="ARCHIVE_SET"):
        stage_repository_workspace_archive(
            inputs["context"], snapshot=inputs["snapshot"], selected_paths=inputs["selected_paths"],
            baseline_files=inputs["baseline_files"], archive_bytes=poisoned_archive,
            workspace_root=root, validate_path=inputs["validate_path"],
            max_files=2, max_archive_bytes=4096,
        )
    assert not root.exists()

    with pytest.raises(ValueError, match="REPOSITORY_PATCH_SCOPE"):
        stage_repository_workspace_archive(
            inputs["context"], snapshot=inputs["snapshot"], selected_paths=[PATH, read_only],
            baseline_files={PATH: BEFORE, read_only: read_only_content},
            archive_bytes=poisoned_archive, workspace_root=root,
            validate_path=inputs["validate_path"], max_files=2, max_archive_bytes=4096,
        )
    assert not root.exists()


def test_read_only_selection_requires_allowed_and_read_only_snapshot_scope(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    path = "tests/test_support.py"
    content = "def test_answer(): assert True\n"
    context = _context(allowed_paths=["app/", "tests/"], read_only_paths=["tests/"])
    snapshot = inputs["snapshot"].model_copy(update={
        "file_manifest": {**inputs["snapshot"].file_manifest, path: _sha256(content)},
    })
    validate = Mock(return_value=None)

    selected = select_repository_read_only_files(
        context, snapshot=snapshot, selected_paths=[path], baseline_files={path: content},
        validate_path=validate, max_files=2, max_bytes=1024,
    )
    assert selected == {path: content}
    validate.assert_called_once_with(path)

    for denied_context in (
        _context(allowed_paths=["app/"], read_only_paths=["tests/"]),
        _context(allowed_paths=["app/", "tests/"], read_only_paths=[]),
        _context(allowed_paths=["app/", "tests/"], read_only_paths=["tests/"],
                 prohibited_paths=["tests/test_support.py"]),
    ):
        with pytest.raises(ValueError, match="INSPECTION_SCOPE"):
            select_repository_read_only_files(
                denied_context, snapshot=snapshot, selected_paths=[path], baseline_files={path: content},
                validate_path=validate, max_files=2, max_bytes=1024,
            )


def test_read_only_selection_rejects_stale_or_unbounded_content(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    path = "tests/test_support.py"
    content = "expected\n"
    context = _context(allowed_paths=["app/", "tests/"], read_only_paths=["tests/"])
    snapshot = inputs["snapshot"].model_copy(update={
        "file_manifest": {**inputs["snapshot"].file_manifest, path: _sha256(content)},
    })
    kwargs = dict(context=context, snapshot=snapshot, selected_paths=[path],
                  baseline_files={path: content}, validate_path=lambda _path: None,
                  max_files=1, max_bytes=1024)
    with pytest.raises(ValueError, match="BASELINE_HASH"):
        select_repository_read_only_files(**{**kwargs, "baseline_files": {path: "stale\n"}})
    with pytest.raises(ValueError, match="SNAPSHOT_IDENTITY"):
        select_repository_read_only_files(**{**kwargs, "snapshot": snapshot.model_copy(update={"plan_id": "other"})})
    with pytest.raises(ValueError, match="INSPECTION_BUDGET"):
        select_repository_read_only_files(**{**kwargs, "max_bytes": 3})
    with pytest.raises(ValueError, match="INSPECTION_SET"):
        select_repository_read_only_files(**{**kwargs, "baseline_files": {path: content, PATH: BEFORE}})
    with pytest.raises(ValueError, match="HOST_POLICY"):
        select_repository_read_only_files(**{**kwargs, "validate_path": lambda _path: False})


@pytest.mark.parametrize("path", [".env.example", "app/security/secrets.yaml", "tests/token.txt"])
def test_read_only_selection_rejects_sensitive_context_path(path: str) -> None:
    context = _context(allowed_paths=["."], read_only_paths=["."])
    snapshot = RepositorySnapshotEvidence(
        plan_id=context.plan_id, request_id=context.request_id, app_id=context.app_id,
        repository_full_name=context.repository_full_name,
        baseline_commit_sha=context.baseline_commit_sha, snapshot_digest=context.snapshot_digest,
        file_manifest={path: _sha256("content")},
    )
    with pytest.raises(ValueError, match="SECRET_PATH|ENV_TEMPLATE"):
        select_repository_read_only_files(
            context, snapshot=snapshot, selected_paths=[path], baseline_files={path: "content"},
            validate_path=lambda _path: None, max_files=1, max_bytes=1024,
        )

def test_changed_secret_contract_cannot_enter_archive_or_review_candidate(tmp_path: Path) -> None:
    path = "app/security/secrets.yaml"
    before = "version: 1\nkind: app_secret_contract\nprovider:\n  type: env\nsecrets: []\n"
    after = before + "raw_token: sk-sentinel\n"
    context = _context(allowed_paths=["app/security/"])
    snapshot = RepositorySnapshotEvidence(
        plan_id=context.plan_id, request_id=context.request_id, app_id=context.app_id,
        repository_full_name=context.repository_full_name,
        baseline_commit_sha=context.baseline_commit_sha, snapshot_digest=context.snapshot_digest,
        file_manifest={path: _sha256(before)},
    )
    workspace = materialize_coding_workspace({path: before}, workspace_root=tmp_path / "staged")
    (workspace.workspace_root / path).write_bytes(after.encode())
    proposal = _proposal(
        owned_paths=[path], changed_files=[ProposedFileChange(path=path, content=after)]
    )
    with pytest.raises(SecretContractError, match="names-only") as export_error:
        export_repository_workspace_archive(workspace, max_files=1, max_archive_bytes=4096)
    assert "sk-sentinel" not in str(export_error.value)
    with pytest.raises(SecretContractError, match="names-only"):
        finalize_repository_patch(
            context, snapshot=snapshot, selected_paths=[path], baseline_files={path: before},
            workspace=workspace, proposal=proposal, validate_path=lambda path: None,
        )

    forged_archive = build_deterministic_archive([ArchiveEntry(path=path, content=after.encode())])
    root = tmp_path / "reconstructed"
    with pytest.raises(SecretContractError, match="names-only"):
        stage_repository_workspace_archive(
            context, snapshot=snapshot, selected_paths=[path], baseline_files={path: before},
            archive_bytes=forged_archive, workspace_root=root, validate_path=lambda path: None,
            max_files=1, max_archive_bytes=4096,
        )
    assert not root.exists()


@pytest.mark.parametrize("path", [".env.example", ".ENV.EXAMPLE", ".env.staging.example", ".env.production.example"])
def test_repository_patch_rejects_env_templates_until_names_only_gate_exists(
    tmp_path: Path, path: str,
) -> None:
    before = "OPENAI_API_KEY=\n"
    after = "OPENAI_API_KEY=sk-sentinel\n"
    context = _context(allowed_paths=[path])
    snapshot = RepositorySnapshotEvidence(
        plan_id=context.plan_id, request_id=context.request_id, app_id=context.app_id,
        repository_full_name=context.repository_full_name,
        baseline_commit_sha=context.baseline_commit_sha, snapshot_digest=context.snapshot_digest,
        file_manifest={path: _sha256(before)},
    )
    workspace = materialize_coding_workspace({path: before}, workspace_root=tmp_path / "staged")
    (workspace.workspace_root / path).write_bytes(after.encode())
    proposal = _proposal(
        owned_paths=[path], changed_files=[ProposedFileChange(path=path, content=after)]
    )
    with pytest.raises(ValueError, match="REPOSITORY_PATCH_ENV_TEMPLATE"):
        export_repository_workspace_archive(workspace, max_files=1, max_archive_bytes=4096)
    with pytest.raises(ValueError, match="REPOSITORY_PATCH_ENV_TEMPLATE"):
        finalize_repository_patch(
            context, snapshot=snapshot, selected_paths=[path], baseline_files={path: before},
            workspace=workspace, proposal=proposal, validate_path=lambda path: None,
        )
    forged_archive = build_deterministic_archive([ArchiveEntry(path=path, content=after.encode())])
    root = tmp_path / "reconstructed"
    with pytest.raises(ValueError, match="REPOSITORY_PATCH_ENV_TEMPLATE"):
        stage_repository_workspace_archive(
            context, snapshot=snapshot, selected_paths=[path], baseline_files={path: before},
            archive_bytes=forged_archive, workspace_root=root,
            validate_path=lambda path: None, max_files=1, max_archive_bytes=4096,
        )
    assert not root.exists()


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
