"""Guard active repo surfaces against stale AG2 migration terminology."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_SKIP_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "generated",
    "htmlcov",
    "logs",
    "node_modules",
    "tmp",
}
_SKIP_FILES = {
    "CHANGELOG.md",
    Path(__file__).name,
}
_FORBIDDEN_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"autogen\.beta",
        r"autogen\.a2a",
        r"\bfrom\s+autogen\b",
        r"\bimport\s+autogen\b",
        r"\bpyautogen\b",
        r"\bConversableAgent\b",
        r"\bGroupChatManager\b",
        r"\bLocalShellTool\b",
        r"\bAG2RunnerAdapter\b",
        r"\bAG2 beta\b",
        r"\bAutoGen\b",
        r"\bautogen_ai_agents\b",
        r"formerly\s+autogen",
        r"old\s+AG2\s+hook",
        r"classic\s+AG2",
        r"removed\s+ContextExpression",
        r"autogen\.cache",
        r"autogen\.logger",
    ]
]


def _tracked_text_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files"],
        cwd=_ROOT,
        text=True,
        encoding="utf-8",
    )
    files: list[Path] = []
    for rel in output.splitlines():
        path = _ROOT / rel
        if not path.exists():
            continue
        if path.name in _SKIP_FILES:
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        if any(part in _SKIP_PARTS for part in path.relative_to(_ROOT).parts):
            continue
        files.append(path)
    return files


def test_active_repo_surfaces_do_not_reintroduce_legacy_ag2_terms() -> None:
    matches: list[str] = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in _FORBIDDEN_PATTERNS):
                rel = path.relative_to(_ROOT).as_posix()
                matches.append(f"{rel}:{line_no}: {line.strip()}")

    assert not matches, "Stale AG2 migration terminology found:\n" + "\n".join(matches)
