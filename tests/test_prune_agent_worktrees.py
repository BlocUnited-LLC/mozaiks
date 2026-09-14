from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "prune_agent_worktrees", ROOT / "scripts" / "prune_agent_worktrees.py"
)
prune = importlib.util.module_from_spec(_spec)
# Register before exec: dataclass field resolution reads sys.modules.
sys.modules["prune_agent_worktrees"] = prune
_spec.loader.exec_module(prune)


def test_squash_merged_branch_counts_as_landed():
    # A squash merge leaves the branch a non-ancestor of main, so git-only
    # ancestry reports finished work as unmerged.
    assert prune.landed("cc/done", merged={"cc/done"}, in_main=set()) is True


def test_ancestor_of_main_counts_as_landed():
    assert prune.landed("cc/done", merged=set(), in_main={"cc/done"}) is True


def test_unlanded_branch_is_not_landed():
    assert prune.landed("cc/wip", merged={"cc/other"}, in_main={"main"}) is False


def test_missing_gh_never_reports_landed_from_pr_state():
    # merged=None means gh was unavailable; the prune must stay conservative
    # rather than treat unknown PR state as merged.
    assert prune.landed("cc/done", merged=None, in_main=set()) is False
    assert prune.landed("cc/done", merged=None, in_main={"cc/done"}) is True


@pytest.mark.parametrize("branch", sorted(prune.PROTECTED_BRANCHES))
def test_protected_branches_are_never_planned_for_deletion(branch, monkeypatch):
    monkeypatch.setattr(prune, "merged_pr_branches", lambda: {branch})
    monkeypatch.setattr(prune, "branches_in_main", lambda: {branch})
    monkeypatch.setattr(prune, "list_worktrees", list)
    monkeypatch.setattr(
        prune, "git",
        lambda *a, **k: type("R", (), {"stdout": branch, "stderr": "", "returncode": 0})(),
    )
    assert branch not in prune.build_plan(set()).branches


def test_branch_held_by_a_kept_worktree_is_not_deleted(monkeypatch, tmp_path):
    held = tmp_path / "wip"
    held.mkdir()
    monkeypatch.setattr(prune, "merged_pr_branches", lambda: {"cc/held"})
    monkeypatch.setattr(prune, "branches_in_main", set)
    monkeypatch.setattr(
        prune, "list_worktrees",
        lambda: [prune.Worktree(path=str(held), branch="cc/held")],
    )
    monkeypatch.setattr(prune, "is_clean", lambda path: False)
    monkeypatch.setattr(
        prune, "git",
        lambda *a, **k: type("R", (), {"stdout": "cc/held\n", "stderr": "", "returncode": 0})(),
    )
    plan = prune.build_plan(set())
    assert [t.reason for t in plan.kept] == ["uncommitted changes"]
    assert plan.branches == []
