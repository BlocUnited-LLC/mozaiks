"""Local-only Docker CLI arguments shared by disposable sandbox adapters."""

from __future__ import annotations

import os


def _local_docker_endpoint() -> str:
    return "npipe:////./pipe/docker_engine" if os.name == "nt" else "unix:///var/run/docker.sock"


def _docker_prefix(config_dir: str) -> list[str]:
    return ["docker", "--config", config_dir, "--host", _local_docker_endpoint()]


def _docker_cli_env() -> dict[str, str]:
    """Drop contexts, remote hosts, inherited auth, and provider credentials."""
    allowed = ("PATH", "PATHEXT", "SystemRoot", "WINDIR") if os.name == "nt" else ("PATH",)
    return {key: os.environ[key] for key in allowed if key in os.environ}
