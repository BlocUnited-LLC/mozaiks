"""The checked-in environment example must support a fresh local start."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ENV = REPO_ROOT / ".env.example"


@pytest.mark.parametrize("blank_timeout", [False, True])
def test_example_env_imports_all_hosts(blank_timeout: bool) -> None:
    values = dotenv_values(EXAMPLE_ENV, interpolate=False)
    assert values
    env = {key: value for key, value in os.environ.items() if not key.startswith("COV_CORE_")}
    env.update({key: value or "" for key, value in values.items()})
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["MONGO_URI"] = os.environ.get("MONGO_URI", "mongodb://localhost:27302")
    if blank_timeout:
        env["DOCKER_SANDBOX_TIMEOUT"] = ""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import mozaiksai.hosts.runtime; import mozaiksai.hosts.platform; "
            "import mozaiksai.hosts.studio; "
            "from mozaiksai.core.adapters.docker_sandbox import _DEFAULT_TIMEOUT_SECONDS; "
            "assert _DEFAULT_TIMEOUT_SECONDS == 300",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_example_backend_url_differs_from_vite_frontend_port() -> None:
    values = dotenv_values(EXAMPLE_ENV, interpolate=False)
    vite_config = (REPO_ROOT / "web_shell" / "vite.config.js").read_text(encoding="utf-8")
    match = re.search(r"\bserver:\s*\{\s*port:\s*(\d+)", vite_config)
    assert match is not None
    assert "'/api': { target: apiUrl" in vite_config

    backend_url = urlsplit(values["MOZAIKS_BACKEND_URL"] or "")
    assert backend_url.port is not None
    assert backend_url.port != int(match.group(1))
