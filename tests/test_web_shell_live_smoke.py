from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _live_web_shell_smoke_enabled() -> bool:
    raw = str(os.getenv("RUN_LIVE_WEB_SHELL_SMOKE") or "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@pytest.mark.skipif(
    not _live_web_shell_smoke_enabled(),
    reason="Set RUN_LIVE_WEB_SHELL_SMOKE=1 to run the Playwright responsive UI smoke test",
)
def test_web_shell_responsive_smoke() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    install = subprocess.run(
        ["npm", "--prefix", "web_shell", "run", "playwright:install"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if install.returncode != 0:
        details = "\n".join(part for part in [install.stdout, install.stderr] if part)
        raise AssertionError(f"Playwright browser install failed:\n{details}")

    result = subprocess.run(
        ["npm", "--prefix", "web_shell", "run", "test:responsive-smoke"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        details = "\n".join(part for part in [result.stdout, result.stderr] if part)
        raise AssertionError(f"Responsive Playwright smoke failed:\n{details}")
