"""The quickstart workspace is generated output and must stay out of the repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _is_ignored(relative_path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", relative_path],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def test_quickstart_workspace_is_gitignored() -> None:
    for relative_path in (
        "mozaiks-workspace/CLAUDE.md",
        "mozaiks-workspace/.claude/rules/runtime.md",
        "mozaiks-workspace/scripts/run-frontend.ps1",
    ):
        assert _is_ignored(relative_path), f"{relative_path} must be gitignored"


def test_repo_owned_files_stay_tracked() -> None:
    for relative_path in ("CLAUDE.md", "README.md", ".claude/rules/runtime.md"):
        assert not _is_ignored(relative_path), f"{relative_path} must stay tracked"
