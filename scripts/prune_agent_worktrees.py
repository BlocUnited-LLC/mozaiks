"""Remove agent worktrees and local branches whose work has already landed.

Reports by default; pass --apply to delete. Anything with uncommitted changes,
or whose work is not provably on main, is always kept.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_BRANCHES = {"main", "master"}


def git(*args: str, cwd: Path | str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=str(cwd or REPO_ROOT)
    )


@dataclass
class Worktree:
    path: str
    branch: str | None = None
    verdict: str = ""
    reason: str = ""


@dataclass
class Plan:
    removable: list[Worktree] = field(default_factory=list)
    kept: list[Worktree] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)


def list_worktrees() -> list[Worktree]:
    out = git("worktree", "list", "--porcelain").stdout
    trees: list[Worktree] = []
    current: Worktree | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            if current:
                trees.append(current)
            current = Worktree(path=line[len("worktree ") :])
        elif line.startswith("branch ") and current:
            current.branch = line[len("branch ") :].replace("refs/heads/", "")
    if current:
        trees.append(current)
    return trees


def merged_pr_branches() -> set[str] | None:
    """Head branches of merged PRs, or None when gh is unavailable.

    Required because a squash merge leaves the branch a non-ancestor of main,
    so git alone reports finished work as unmerged.
    """
    probe = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True
    )
    if probe.returncode != 0:
        return None
    result = subprocess.run(
        [
            "gh", "pr", "list", "--state", "merged", "--limit", "500",
            "--json", "headRefName", "-q", ".[].headRefName",
        ],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def is_clean(path: str) -> bool:
    result = git("status", "--porcelain", cwd=path)
    return result.returncode == 0 and not result.stdout.strip()


def landed(branch: str, merged: set[str] | None, in_main: set[str]) -> bool:
    if branch in in_main:
        return True
    return merged is not None and branch in merged


def branches_in_main() -> set[str]:
    out = git("branch", "--merged", "origin/main", "--format=%(refname:short)").stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def build_plan(keep_paths: set[str]) -> Plan:
    merged = merged_pr_branches()
    if merged is None:
        print(
            "warning: gh unavailable — squash-merged branches will be kept as "
            "unmerged. Install/authenticate gh for a complete prune.",
            file=sys.stderr,
        )
    in_main = branches_in_main()
    primary = os.path.abspath(str(REPO_ROOT))
    current = git("branch", "--show-current").stdout.strip()

    plan = Plan()
    for tree in list_worktrees():
        abspath = os.path.abspath(tree.path)
        if abspath == primary:
            continue
        if abspath in keep_paths:
            tree.verdict, tree.reason = "keep", "excluded by --keep"
        elif not os.path.isdir(tree.path):
            tree.verdict, tree.reason = "remove", "directory already gone"
        elif not tree.branch:
            tree.verdict, tree.reason = "keep", "detached HEAD"
        elif not is_clean(tree.path):
            tree.verdict, tree.reason = "keep", "uncommitted changes"
        elif not landed(tree.branch, merged, in_main):
            tree.verdict, tree.reason = "keep", "work not on main"
        else:
            tree.verdict, tree.reason = "remove", "clean, already on main"
        (plan.removable if tree.verdict == "remove" else plan.kept).append(tree)

    held = {t.branch for t in plan.kept if t.branch}
    for line in git("branch", "--format=%(refname:short)").stdout.splitlines():
        branch = line.strip()
        if not branch or branch in PROTECTED_BRANCHES or branch == current:
            continue
        if branch in held or not landed(branch, merged, in_main):
            continue
        plan.branches.append(branch)
    return plan


def apply_plan(plan: Plan) -> tuple[int, int, list[str]]:
    removed = 0
    failures: list[str] = []
    for tree in plan.removable:
        # Re-check immediately before deleting; state can change after planning.
        if os.path.isdir(tree.path) and not is_clean(tree.path):
            failures.append(f"{tree.path}: became dirty, skipped")
            continue
        result = git("worktree", "remove", "--force", tree.path)
        if result.returncode == 0:
            removed += 1
        else:
            failures.append(f"{tree.path}: {result.stderr.strip().splitlines()[0]}")
    git("worktree", "prune")

    deleted = 0
    for branch in plan.branches:
        if git("branch", "-D", branch).returncode == 0:
            deleted += 1
        else:
            failures.append(f"branch {branch}: still checked out")
    return removed, deleted, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete; default reports only")
    parser.add_argument("--keep", action="append", default=[], metavar="PATH",
                        help="worktree path to preserve (repeatable)")
    parser.add_argument("--json", action="store_true", help="emit the plan as JSON")
    args = parser.parse_args()

    if git("rev-parse", "--git-dir").returncode != 0:
        print("error: not a git repository", file=sys.stderr)
        return 2
    git("fetch", "--prune", "origin")

    keep_paths = {os.path.abspath(p) for p in args.keep}
    plan = build_plan(keep_paths)

    if args.json:
        print(json.dumps({
            "remove": [{"path": t.path, "branch": t.branch} for t in plan.removable],
            "keep": [{"path": t.path, "branch": t.branch, "reason": t.reason} for t in plan.kept],
            "branches": plan.branches,
        }, indent=2))
        return 0

    print(f"worktrees to remove: {len(plan.removable)}")
    for tree in plan.removable:
        print(f"  - {tree.path}  [{tree.branch or 'detached'}]")
    print(f"worktrees kept: {len(plan.kept)}")
    for tree in plan.kept:
        print(f"  = {tree.path}  [{tree.branch or 'detached'}] — {tree.reason}")
    print(f"local branches to delete: {len(plan.branches)}")

    if not args.apply:
        print("\ndry run — nothing deleted. Re-run with --apply.")
        return 0

    removed, deleted, failures = apply_plan(plan)
    print(f"\nremoved {removed} worktree(s), deleted {deleted} branch(es)")
    for failure in failures:
        print(f"  ! {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
