from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_EXACT_TRACKED_PATHS = {
    ".pytest_live_smoke.err.txt",
    "package-lock.json",
    "runtime_tool_call_debug.txt",
    "test_chat.html",
    "valueengine_debug.txt",
}

FORBIDDEN_TRACKED_PREFIXES = (
    ".tmp/",
    "logs/agent_outputs/",
    "logs/logs/",
    "logs/workflow_converter/",
    "site/",
    "tmp/",
    "web_shell/playwright/tmp-generated",
)

FORBIDDEN_TRACKED_PARTS = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
}

FORBIDDEN_TRACKED_PATTERNS = (
    "*.log",
    "*.pyc",
    "*.pyo",
    "*_debug.txt",
    ".pytest_live_smoke*.txt",
)


def _tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def _stale_artifact_reason(path: str) -> str | None:
    if path in FORBIDDEN_EXACT_TRACKED_PATHS:
        return "one-off debug or root-local artifact"
    if path.startswith(FORBIDDEN_TRACKED_PREFIXES):
        return "generated output directory"
    if any(part in FORBIDDEN_TRACKED_PARTS for part in path.split("/")):
        return "cache or dependency directory"
    if path.startswith(("chat-ui/dist/", "web_shell/dist/")):
        return "frontend build output"
    if any(fnmatch.fnmatch(path, pattern) for pattern in FORBIDDEN_TRACKED_PATTERNS):
        return "generated log/cache artifact"
    return None


def test_local_generated_artifacts_are_not_tracked() -> None:
    hits = [
        f"{path}: {_stale_artifact_reason(path)}"
        for path in _tracked_paths()
        if _stale_artifact_reason(path)
    ]

    assert not hits, "Stale local artifacts are tracked:\n" + "\n".join(hits[:200])
