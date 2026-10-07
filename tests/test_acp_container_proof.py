"""Opt-in offline proof that AG2's ACP bridge and its subprocess share a container.

Build ``mozaiks-acp-proof:local`` with ``infra/docker/Dockerfile.acp-proof`` and
set ``MOZAIKS_RUN_ACP_CONTAINER_PROOF=1`` to run this test. No model, API key,
host repository mount, or network connection is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from mozaiksai.control_plane import (
    CodingWorkerRequest,
    ControlPlaneCodingCapabilityConfig,
    ControlPlaneConfig,
    ScopedRefinementCodingWorker,
    StagedPatchProposal,
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


class DockerACPProofProvider:
    """Test-only CodingExecutionProvider; Docker receives scoped JSON on stdin."""

    provider_id = "acp_claude_code"

    def __init__(self) -> None:
        self.container_config: dict[str, Any] = {}

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
            assert len(run.stdout) < 1_000_000, "container proposal exceeded proof limit"
            return StagedPatchProposal.model_validate_json(run.stdout)
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
        coding=ControlPlaneCodingCapabilityConfig.model_validate(
            {"enabled": True, "providers": {"acp": {"enabled": True}}}
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
        "host_sentinel_visible": False,
        "terminal": {"host_secret_visible": False, "outbound_reachable": False},
    }
