"""Opt-in offline proof that AG2's ACP bridge and its subprocess share a container.

Build ``mozaiks-acp-proof:local`` with ``infra/docker/Dockerfile.acp-proof`` and
set ``MOZAIKS_RUN_ACP_CONTAINER_PROOF=1`` to run this test. No model, API key,
host repository mount, or network connection is used.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from mozaiksai.control_plane import (
    ApprovedExecutionContext,
    CodingWorkerRequest,
    ControlPlaneCodingCapabilityConfig,
    ControlPlaneConfig,
    RepositorySnapshotEvidence,
    ScopedRefinementCodingWorker,
    StagedPatchProposal,
    finalize_repository_patch,
    select_repository_read_only_files,
    stage_repository_workspace_archive,
)
from mozaiksai.core.artifacts.models import BuildRecord

pytestmark = pytest.mark.skipif(
    os.getenv("MOZAIKS_RUN_ACP_CONTAINER_PROOF") != "1",
    reason="opt-in Docker ACP isolation proof",
)

_IMAGE = "mozaiks-acp-proof:local"
_EDITABLE = "app/ui/pages/Dashboard.jsx"
_ORIGINAL = "export default function Dashboard() {}\n"
_PATCHED = "export default function Dashboard() { return 1; }\n"
_READ_ONLY = "tests/test_dashboard.py"
_READ_ONLY_CONTENT = "def test_dashboard(): assert True\n"
_MAX_ARCHIVE_BYTES = 700_000


class DockerACPProofProvider:
    """Test-only CodingExecutionProvider; Docker receives scoped JSON on stdin."""

    provider_id = "acp_claude_code"

    def __init__(self) -> None:
        self.container_config: dict[str, Any] = {}
        self.archive_bytes: bytes | None = None
        self.proposal: StagedPatchProposal | None = None

    async def execute(self, request: CodingWorkerRequest) -> StagedPatchProposal:
        return await asyncio.to_thread(self._execute, request)

    def _execute(self, request: CodingWorkerRequest) -> StagedPatchProposal:
        # Deliberately omit baseline, user identity, build binding, and metadata.
        # The container needs only the approved edit set and task instruction.
        scoped_request = {
            "app_id": request.app_id,
            "build_family": request.build_family,
            "build_record_id": request.build_record_id,
            "change_class": request.change_class,
            "raw_user_request": request.raw_user_request,
            "files": request.files,
            "read_only_files": request.read_only_files,
        }
        input_bytes = json.dumps(scoped_request).encode("utf-8")
        assert len(input_bytes) < 1_000_000
        create = subprocess.run(
            [
                "docker", "create", "--interactive", "--init",
                "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "64",
                "--memory", "768m", "--cpus", "1",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
                "--tmpfs", "/workspace:rw,nosuid,nodev,size=64m,mode=1777",
                "--tmpfs", "/home/sandbox:rw,nosuid,nodev,size=16m",
                "--user", "10001:10001", _IMAGE,
            ],
            capture_output=True, text=True, timeout=30, check=True,
        )
        container_id = create.stdout.strip()
        try:
            inspect = subprocess.run(
                ["docker", "inspect", container_id],
                capture_output=True, text=True, timeout=30, check=True,
            )
            self.container_config = json.loads(inspect.stdout)[0]
            host = self.container_config["HostConfig"]
            assert host["NetworkMode"] == "none"
            assert host["ReadonlyRootfs"] is True
            assert host["Binds"] is None
            assert not any(mount["Type"] in {"bind", "volume"} for mount in self.container_config["Mounts"])
            assert host["CapDrop"] == ["ALL"]
            assert "no-new-privileges" in host["SecurityOpt"]
            assert host["PidsLimit"] == 64
            assert host["Memory"] == 768 * 1024 * 1024
            assert host["NanoCpus"] == 1_000_000_000
            assert self.container_config["Config"]["User"] == "10001:10001"
            assert not any(
                item.startswith("MOZAIKS_ACP_HOST_SECRET=")
                for item in self.container_config["Config"]["Env"]
            )
            run = subprocess.run(
                ["docker", "start", "--attach", "--interactive", container_id],
                input=input_bytes, capture_output=True, timeout=75, check=True,
            )
            assert len(run.stdout) < 1_000_000, "container output exceeded proof limit"
            output = json.loads(run.stdout)
            assert set(output) == {"proposal", "workspace_archive_base64"}
            encoded_archive = output["workspace_archive_base64"]
            self.archive_bytes = (
                base64.b64decode(encoded_archive, validate=True) if encoded_archive is not None else None
            )
            assert self.archive_bytes is None or len(self.archive_bytes) <= _MAX_ARCHIVE_BYTES
            self.proposal = StagedPatchProposal.model_validate(output["proposal"])
            return self.proposal
        finally:
            removed = subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, timeout=30)
            assert removed.returncode == 0, "disposable ACP container was not removed"


class _ArtifactStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_build_record(self, **kwargs: Any) -> BuildRecord:
        self.calls.append(kwargs)
        return BuildRecord(id="av_child", version_number=1, lineage_root_id="av_parent", **kwargs)


async def _validate_candidate(**kwargs: Any) -> dict[str, Any]:
    assert kwargs["files"]["app/app.json"] == '{"appId":"proof"}'
    assert kwargs["files"][_EDITABLE] == _PATCHED
    return {
        "validation_status": "passed",
        "validation_strategy": kwargs["validation_strategy"],
        "app_bundle_acceptance_result": {"passed": True, "validation_evidence": {"completed": ["offline_proof"]}},
        "app_validation_result": {"validation_status": "passed", "validation_strategy": kwargs["validation_strategy"]},
    }


@pytest.mark.asyncio
async def test_acp_client_adapter_and_terminal_are_confined_to_disposable_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("MOZAIKS_ACP_HOST_SECRET", "host-only-sentinel")
    monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", "local")
    provider = DockerACPProofProvider()
    store = _ArtifactStore()
    policy = ControlPlaneConfig(
        enabled=True,
        llm_profiles={"codegen": {"llm_config": {"model": "test-model"}}},
        coding=ControlPlaneCodingCapabilityConfig.model_validate(
            {"enabled": True, "llm_profile": "codegen", "providers": {"acp": {"enabled": True}}}
        ),
    )
    worker = ScopedRefinementCodingWorker(
        acp_provider=provider,
        config_loader=lambda: policy,
        candidate_validation_runner=_validate_candidate,
        artifact_store=store,
        output_root=tmp_path / "artifacts",
    )
    request = CodingWorkerRequest(
        app_id="proof", build_family="app_bundle", build_record_id="av_parent",
        change_class="patch", raw_user_request="Make the dashboard return 1",
        files={_EDITABLE: _ORIGINAL, "app/brand/theme_config.json": '{"accent":"blue"}'},
        baseline_files={
            "app/app.json": '{"appId":"proof"}',
            _EDITABLE: _ORIGINAL,
            "app/brand/theme_config.json": '{"accent":"blue"}',
        },
    )

    host_sentinel = Path.cwd() / ".host_sentinel"
    assert not host_sentinel.exists()
    host_sentinel.write_text("host-only-file", encoding="utf-8")
    try:
        result = await worker.execute(request)
    finally:
        host_sentinel.unlink()

    assert result.status == "validated", result.error
    assert result.applied_files == {_EDITABLE: _PATCHED}
    assert len(store.calls) == 1
    assert result.metadata["coding_provider_attempts"][0]["provider"] == provider.provider_id
    assert result.metadata["coding_provider_attempts"][0]["status"] == "completed"
    proof = json.loads(result.plan.summary)
    assert proof == {
        "adapter_host_secret_visible": False,
        "adapter_outbound_reachable": False,
        "baseline_file_visible": False,
        "read_only_test_visible": False,
        "host_sentinel_visible": False,
        "terminal": {"host_secret_visible": False, "outbound_reachable": False},
    }

    # The same isolated ACP turn must also support repository refinement.
    # The host reconstructs a fresh tree from the approved baseline, never
    # accepting the provider's claimed changed-file content as observation.
    context = ApprovedExecutionContext.model_validate({
        "handoff_id": "handoff-proof", "request_id": "request-proof", "plan_id": "plan-proof",
        "app_id": "proof", "build_registry_id": "registry-proof",
        "repository_full_name": "example/app", "baseline_commit_sha": "a" * 40,
        "raw_request": request.raw_user_request, "request_type": "patch",
        "approved_plan_digest": "b" * 64, "snapshot_digest": "sha256:" + "c" * 64,
        "graph_identity_digest": "graph-proof", "impact_report_digest": "impact-proof",
        "execution_strategy_id": "standard_coding_refinement", "rationale": "approved proof",
        "allowed_paths": ["app/", "tests/"], "read_only_paths": ["tests/"],
        "required_validation_gates": ["pytest"], "required_security_gates": ["secret_scan"],
        "required_ci_checks": ["lint"],
    })
    baseline_files = dict(request.files)
    snapshot = RepositorySnapshotEvidence(
        plan_id=context.plan_id, request_id=context.request_id, app_id=context.app_id,
        repository_full_name=context.repository_full_name,
        baseline_commit_sha=context.baseline_commit_sha,
        snapshot_digest=context.snapshot_digest,
        file_manifest={
            path: "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in {**baseline_files, _READ_ONLY: _READ_ONLY_CONTENT}.items()
        },
    )
    assert provider.archive_bytes is not None
    assert provider.proposal is not None

    def host_policy(path: str) -> None:
        assert path in baseline_files

    staged = stage_repository_workspace_archive(
        context, snapshot=snapshot, selected_paths=list(baseline_files),
        baseline_files=baseline_files, archive_bytes=provider.archive_bytes,
        workspace_root=tmp_path / "repository_staged", validate_path=host_policy,
        max_files=3, max_archive_bytes=_MAX_ARCHIVE_BYTES,
    )
    try:
        candidate = finalize_repository_patch(
            context, snapshot=snapshot, selected_paths=list(baseline_files),
            baseline_files=baseline_files, workspace=staged,
            proposal=provider.proposal,
            validate_path=host_policy,
        )
    finally:
        staged.cleanup()
    assert candidate.validation_state == "pending"
    assert candidate.mutation_allowed is False
    assert candidate.changed_files[0].path == _EDITABLE
    assert candidate.changed_files[0].content == _PATCHED
    assert candidate.changed_files[0].previous_sha256 == snapshot.file_manifest[_EDITABLE]
    assert _READ_ONLY in snapshot.file_manifest
    assert _READ_ONLY not in baseline_files
    assert all(change.path != _READ_ONLY for change in candidate.changed_files)

    # Repository mode adds only snapshot-verified read-only inspection bytes.
    selected_inspection = select_repository_read_only_files(
        context, snapshot=snapshot, selected_paths=[_READ_ONLY],
        baseline_files={_READ_ONLY: _READ_ONLY_CONTENT},
        validate_path=lambda path: None if path == _READ_ONLY else False,
        max_files=1, max_bytes=1024,
    )
    repository_provider = DockerACPProofProvider()
    repository_proposal = await repository_provider.execute(request.model_copy(update={
        "read_only_files": selected_inspection,
    }))
    assert repository_proposal.status == "completed", repository_proposal.error
    repository_proof = json.loads(repository_proposal.summary)
    assert repository_proof["read_only_test_visible"] is True
    assert repository_proof["host_sentinel_visible"] is False
    assert repository_provider.archive_bytes is not None
    from mozaiksai.core.semantics.archive import read_archive_manifest
    assert {entry.path for entry in read_archive_manifest(repository_provider.archive_bytes).entries} == set(baseline_files)
    staged_inspection = stage_repository_workspace_archive(
        context, snapshot=snapshot, selected_paths=list(baseline_files),
        baseline_files=baseline_files, archive_bytes=repository_provider.archive_bytes,
        workspace_root=tmp_path / "repository_inspection_staged", validate_path=host_policy,
        max_files=3, max_archive_bytes=_MAX_ARCHIVE_BYTES,
    )
    try:
        inspected_candidate = finalize_repository_patch(
            context, snapshot=snapshot, selected_paths=list(baseline_files),
            baseline_files=baseline_files, workspace=staged_inspection,
            proposal=repository_proposal, validate_path=host_policy,
        )
    finally:
        staged_inspection.cleanup()
    assert [change.path for change in inspected_candidate.changed_files] == [_EDITABLE]
    assert _READ_ONLY_CONTENT not in inspected_candidate.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,violation", [
    ("edit", "read_only_modified"), ("delete", "read_only_missing"),
])
async def test_container_rejects_read_only_changes_without_archive(
    tmp_path: Path, operation: str, violation: str,
) -> None:
    provider = DockerACPProofProvider()
    proposal = await provider.execute(CodingWorkerRequest(
        app_id="proof", build_family="app_bundle", build_record_id="av_parent",
        change_class="patch", raw_user_request="Make the dashboard return 1",
        files={_EDITABLE: _ORIGINAL},
        read_only_files={_READ_ONLY: f"# proof: {operation}-read-only\n"},
    ))

    assert proposal.status == "rejected_scope"
    assert violation in (proposal.error or "")
    assert provider.archive_bytes is None
    assert proposal.changed_files == []
