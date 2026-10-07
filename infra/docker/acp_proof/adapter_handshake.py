"""Initialize a pinned real ACP adapter offline using AG2's command preset."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import acp
from acp import schema
from ag2.acp import ClaudeCodeConfig, CodexConfig

_WORKSPACE = Path("/workspace")
_PACKAGE_ROOT = Path("/opt/acp/node_modules/@agentclientprotocol")
_PRESETS = {"claude_code": ClaudeCodeConfig, "codex": CodexConfig}
_PACKAGES = {"claude_code": "claude-agent-acp", "codex": "codex-acp"}
_EXPECTED = {"claude_code": "0.86.0", "codex": "2.1.1"}
_CREDENTIAL_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY")


class _InitializationClient:
    """An initialize-only ACP client; no filesystem or terminal capabilities."""

    def on_connect(self, _connection: object) -> None:
        pass


def _installed_adapter_version(adapter: str) -> str:
    manifest = _PACKAGE_ROOT / _PACKAGES[adapter] / "package.json"
    return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])


async def _initialize(adapter: str) -> dict[str, object]:
    if adapter not in _PRESETS:
        raise ValueError(f"unknown ACP adapter: {adapter}")
    if any(os.environ.get(name) for name in _CREDENTIAL_NAMES):
        raise RuntimeError("the offline ACP handshake must not receive a model credential")

    version = _installed_adapter_version(adapter)
    if version != _EXPECTED[adapter]:
        raise RuntimeError(f"unexpected {adapter} adapter version: {version}")

    preset = _PRESETS[adapter](
        cwd=str(_WORKSPACE),
        fs_root=str(_WORKSPACE),
        env=None,
        permission_policy="deny",
        elicitation_policy="decline",
        expose_tools=False,
        allow_terminal=False,
        turn_timeout=20.0,
    )
    command = preset.command
    if command != [_PACKAGES[adapter]]:
        raise RuntimeError(f"AG2 command preset drifted for {adapter}: {command}")

    child_env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "CI": "1",
        "NO_BROWSER": "1",
        "CLAUDE_CONFIG_DIR": "/home/sandbox/.claude",
        "CODEX_HOME": "/home/sandbox/.codex",
    }
    Path(child_env["CODEX_HOME"]).mkdir(parents=True, exist_ok=True)
    Path(child_env["CLAUDE_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
    async with acp.spawn_agent_process(
        _InitializationClient(), *command, env=child_env, cwd=_WORKSPACE
    ) as (connection, _process):
        response = await connection.initialize(
            protocol_version=acp.PROTOCOL_VERSION,
            client_capabilities=schema.ClientCapabilities(
                fs=schema.FileSystemCapabilities(read_text_file=False, write_text_file=False),
                terminal=False,
            ),
        )

    if response.protocol_version != acp.PROTOCOL_VERSION:
        raise RuntimeError(f"ACP protocol mismatch: {response.protocol_version}")
    if response.agent_info is None or not response.agent_info.name:
        raise RuntimeError("ACP adapter did not identify itself")
    return {
        "adapter": adapter,
        "adapter_version": version,
        "ag2_version": importlib.metadata.version("ag2"),
        "acp_version": importlib.metadata.version("agent-client-protocol"),
        "node_version": subprocess.check_output(["node", "--version"], text=True).strip(),
        "python_version": platform.python_version(),
        "protocol_version": response.protocol_version,
        "agent_name": response.agent_info.name,
        "agent_version": response.agent_info.version,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: adapter_handshake.py {claude_code|codex}")
    result = asyncio.run(asyncio.wait_for(_initialize(sys.argv[1]), timeout=30))
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
