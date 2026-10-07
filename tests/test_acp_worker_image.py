"""Opt-in offline proof for one image containing the worker and real ACP adapters.

Build Dockerfile.preview, Dockerfile.acp-proof, Dockerfile.acp-adapters, then
Dockerfile.acp-worker as ``mozaiks-acp-worker:local``. Set
``MOZAIKS_RUN_ACP_WORKER_IMAGE_PROOF=1``. No model key or network is required.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from mozaiksai.control_plane import CodingWorkerRequest, execute_repository_docker_turn

pytestmark = pytest.mark.skipif(
    os.getenv("MOZAIKS_RUN_ACP_WORKER_IMAGE_PROOF") != "1",
    reason="opt-in offline combined ACP worker image proof",
)

_IMAGE = "mozaiks-acp-worker:local"
_PATH = "app/ui/pages/Dashboard.jsx"
_BEFORE = "export default function Dashboard() {}\n"
_AFTER = "export default function Dashboard() { return 1; }\n"
_ADAPTERS = {
    "codex": ("2.1.1", "@agentclientprotocol/codex-acp"),
    "claude_code": ("0.86.0", "@agentclientprotocol/claude-agent-acp"),
}


@pytest.mark.parametrize("adapter", _ADAPTERS)
def test_combined_image_initializes_real_adapter_offline(
    adapter: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOZAIKS_ACP_HOST_SECRET", "host-only-canary")
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--init",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--log-driver",
            "none",
            "--tmpfs",
            "/workspace:rw,nosuid,nodev,size=64m,mode=0700,uid=10001,gid=10001",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--tmpfs",
            "/home/sandbox:rw,nosuid,nodev,size=64m,mode=0700,uid=10001,gid=10001",
            "--user",
            "10001:10001",
            "--entrypoint",
            "python",
            _IMAGE,
            "/opt/mozaiks/acp_proof/adapter_handshake.py",
            adapter,
        ],
        capture_output=True,
        timeout=45,
        check=True,
    )
    assert len(completed.stdout) <= 4096
    assert len(completed.stderr) <= 16_384
    assert b"host-only-canary" not in completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    version, name = _ADAPTERS[adapter]
    assert result["adapter"] == adapter
    assert result["adapter_version"] == version
    assert result["agent_name"] == name
    assert result["ag2_version"] == "1.1.2"
    assert result["acp_version"] == "0.12.1"
    assert result["node_version"] == "v22.23.3"
    assert result["protocol_version"] == 1


@pytest.mark.asyncio
async def test_combined_image_runs_bounded_fake_turn_without_host_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOZAIKS_ACP_HOST_SECRET", "host-only-canary")
    request = CodingWorkerRequest(
        app_id="proof",
        build_family="app_bundle",
        build_record_id="av_parent",
        change_class="patch",
        raw_user_request="Make the dashboard return 1",
        files={_PATH: _BEFORE},
    )
    turn = await execute_repository_docker_turn(
        request,
        image=_IMAGE,
        max_wall_seconds=75,
        max_archive_bytes=700_000,
    )
    assert turn.proposal.status == "completed"
    assert [(file.path, file.op, file.content) for file in turn.proposal.changed_files] == [
        (_PATH, "update", _AFTER),
    ]
    assert turn.workspace_archive is not None
    assert "host-only-canary" not in turn.proposal.model_dump_json()
