"""Opt-in, credential-free initialize proof for the real ACP adapter binaries.

Build ``mozaiks-acp-adapters:local`` with ``Dockerfile.acp-adapters`` and set
``MOZAIKS_RUN_ACP_ADAPTER_HANDSHAKE=1``. This exercises only ACP initialize;
the existing fake-agent container proof covers AG2 turn and workspace flow.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("MOZAIKS_RUN_ACP_ADAPTER_HANDSHAKE") != "1",
    reason="opt-in offline real ACP adapter handshake",
)

_IMAGE = "mozaiks-acp-adapters:local"
_EXPECTED = {
    "claude_code": ("0.86.0", "@agentclientprotocol/claude-agent-acp"),
    "codex": ("2.1.1", "@agentclientprotocol/codex-acp"),
}


@pytest.mark.parametrize("adapter", ["claude_code", "codex"])
def test_real_acp_adapter_initializes_in_disposable_offline_container(
    adapter: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOZAIKS_ACP_HOST_SECRET", "host-only-sentinel")
    created = subprocess.run(
        [
            "docker", "create", "--init", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "2g", "--memory-swap", "2g",
            "--cpus", "2", "--ulimit", "core=0:0",
            "--ulimit", "nofile=1024:1024", "--log-driver", "none",
            "--tmpfs", "/workspace:rw,nosuid,nodev,size=64m,mode=0700,uid=10001,gid=10001",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--tmpfs", "/home/sandbox:rw,nosuid,nodev,size=64m,mode=0700,uid=10001,gid=10001",
            "--user", "10001:10001", _IMAGE, adapter,
        ],
        capture_output=True, text=True, timeout=30, check=True,
    )
    container_id = created.stdout.strip()
    try:
        inspected = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True, text=True, timeout=30, check=True,
        )
        config: dict[str, Any] = json.loads(inspected.stdout)[0]
        host = config["HostConfig"]
        assert host["NetworkMode"] == "none"
        assert host["ReadonlyRootfs"] is True
        assert host["Binds"] is None
        assert not any(mount["Type"] in {"bind", "volume"} for mount in config["Mounts"])
        assert host["CapDrop"] == ["ALL"]
        assert "no-new-privileges" in host["SecurityOpt"]
        assert host["PidsLimit"] == 128
        assert host["Memory"] == 2 * 1024**3
        assert host["MemorySwap"] == 2 * 1024**3
        assert host["NanoCpus"] == 2_000_000_000
        assert host["LogConfig"]["Type"] == "none"
        assert config["Config"]["User"] == "10001:10001"
        assert not any(
            item.startswith(("ANTHROPIC_API_KEY=", "OPENAI_API_KEY=", "CODEX_API_KEY=", "MOZAIKS_ACP_HOST_SECRET="))
            for item in config["Config"]["Env"]
        )

        started = subprocess.run(
            ["docker", "start", "--attach", container_id],
            capture_output=True, timeout=45, check=True,
        )
        assert len(started.stdout) <= 4096
        assert len(started.stderr) <= 16_384
        assert b"host-only-sentinel" not in started.stdout + started.stderr
        result = json.loads(started.stdout)
        version, name = _EXPECTED[adapter]
        assert result == {
            "acp_version": "0.12.1",
            "adapter": adapter,
            "adapter_version": version,
            "ag2_version": "1.1.2",
            "agent_name": name,
            "agent_version": version,
            "node_version": "v22.23.3",
            "python_version": "3.12.15",
            "protocol_version": 1,
        }
    finally:
        removed = subprocess.run(
            ["docker", "rm", "--force", container_id],
            capture_output=True, timeout=30,
        )
        assert removed.returncode == 0, "disposable ACP adapter container was not removed"
