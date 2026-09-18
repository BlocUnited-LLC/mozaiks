"""Coordination guidance must match how agents actually run.

Both entry points told agents that `mozaiks` belongs to Claude Code and
`mozaiks-app` belongs to Codex. That was never how the repos were worked: two
Claude Code sessions and two Codex sessions run concurrently across both, and
tasks regularly span them. An agent following the stale rule defers work that is
genuinely unclaimed, or assumes a repo is quiet because it is "theirs".

The prefixes cannot carry the load either -- two Claude Code sessions both push
`cc/`, so a prefix identifies the tool, not the worker. Collisions are prevented
by claiming work where the other agents can see it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / ".claude/rules/multi-agent-coordination.md"
AGENTS = ROOT / "AGENTS.md"


@pytest.fixture(scope="module")
def rules() -> str:
    return RULES.read_text(encoding="utf-8")


def test_no_document_assigns_a_repo_to_an_agent() -> None:
    for path in (RULES, AGENTS, ROOT / "CLAUDE.md"):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "| Repo | Primary agent |" not in text, (
            f"{path.name} still assigns repos to agents; both agents work in both repos"
        )
        assert "Primary repo ownership" not in text, f"{path.name} still claims per-repo ownership"


def test_claiming_is_described_because_prefixes_cannot_disambiguate(rules: str) -> None:
    assert "gh pr list" in rules, "agents must be told to check what is in flight"
    assert "draft PR" in rules, (
        "an early draft PR is the visible claim; without it two agents can pick "
        "the same task and only discover it at merge time"
    )
    # The specific failure mode the prefix scheme cannot cover.
    assert "identifies the tool" in rules, (
        "the rule must say why prefixes are insufficient: two sessions of the "
        "same tool share a prefix"
    )


def test_the_shared_runtime_hazard_is_documented(rules: str) -> None:
    """Branch rules do not protect the one stack every agent shares."""
    assert "pip install -r requirements.txt" in rules, (
        "reinstalling in the shared venv silently replaces the OSS package for "
        "every agent; this cost most of a session and must be written down"
    )
    assert "chat_id" in rules, (
        "a concurrent build raises global counts, so results must be attributed "
        "to your own chat rather than to a total"
    )


def test_worktree_isolation_keeps_its_windows_warning(rules: str) -> None:
    assert "worktree" in rules.lower()
    assert "LinkType" in rules, (
        "deleting a worktree follows node_modules junctions and destroys the main "
        "checkout's packages; find -type l does not detect them"
    )
