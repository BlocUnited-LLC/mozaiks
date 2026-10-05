"""The compose stack publishes the app port as before unless the operator narrows it.

Local no-auth mode publishes on 127.0.0.1 only (``MOZAIKS_APP_PORTS``); the
default authenticated stack keeps the unrestricted ``8000:8000`` mapping.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "infra" / "compose" / "docker-compose.yml"


def _docker_compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def test_app_port_publish_is_one_overridable_mapping():
    app = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["app"]
    assert app["ports"] == ["${MOZAIKS_APP_PORTS:-8000:8000}"]
    # An open instance must be asked for explicitly: no posture default here.
    assert "AUTH_ANON_ACCESS" not in app["environment"]


@pytest.mark.skipif(not _docker_compose_available(), reason="docker compose is not installed")
@pytest.mark.parametrize(
    ("env_file_text", "host_ip"),
    [
        ("", None),
        ("MOZAIKS_APP_PORTS=127.0.0.1:8000:8000\n", "127.0.0.1"),
    ],
)
def test_compose_config_publishes_the_app_port(tmp_path, env_file_text, host_ip):
    # A copy, so the service's env_file (../../.env) resolves inside tmp_path.
    compose_dir = tmp_path / "infra" / "compose"
    compose_dir.mkdir(parents=True)
    shutil.copy(COMPOSE, compose_dir / "docker-compose.yml")
    (tmp_path / ".env").write_text(env_file_text, encoding="utf-8")
    environ = {name: value for name, value in os.environ.items() if name != "MOZAIKS_APP_PORTS"}

    result = subprocess.run(
        [
            "docker", "compose", "-f", "docker-compose.yml", "--env-file", "../../.env",
            "config", "--format", "json",
        ],
        cwd=compose_dir,
        env=environ,
        capture_output=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    (port,) = json.loads(result.stdout)["services"]["app"]["ports"]
    assert (port["target"], str(port["published"])) == (8000, "8000")
    assert port.get("host_ip") == host_ip
