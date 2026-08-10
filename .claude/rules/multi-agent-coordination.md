# Multi-Agent Coordination Rules

Use these rules whenever you are about to start work, push, or merge in this repo.
Multiple coding agents (Claude Code, Codex) operate simultaneously across
`mozaiks` and `mozaiks-app`. Without coordination they will stomp on each other.

## Before Starting Any Task

```bash
git fetch origin
git log origin/main --oneline -5   # see what recently landed
gh pr list --state open            # see what other agents have in flight
```

**Never branch off another open PR's branch, even if it looks like a hard
dependency.** Branching on top of unmerged, not-yet-green work means you
inherit its bugs, and every fix it needs later has to be re-propagated into
your branch too — this is how a single in-progress refactor turns into a
cascading chain of broken PRs. Instead:
- Wait for the dependency PR to merge (green CI, actually merged into `main`),
  then branch from fresh `origin/main`.
- If you truly cannot wait, say so explicitly in your PR description ("stacked
  on #123, will rebase once it merges") so reviewers know why checks are red,
  and rebase onto `main` the moment the base PR lands.

**Always work in an isolated worktree, never directly in the shared main
checkout.** Multiple agents run git commands in the same main checkout folder
concurrently, which silently switches branches and sweeps unrelated
uncommitted edits into your commits. Before any nontrivial edit:

```bash
git worktree add .local/worktrees/<task-name> origin/main -b cc/<short-description>
```

Do all work there, and remove it when done (`git worktree remove
.local/worktrees/<task-name> --force`).

## Before Opening a PR — Local Verification Is Mandatory

Do not push and open a PR on faith that CI will catch problems. Run locally
first, in your worktree:

```bash
ruff check .                        # catches unsorted imports / lint issues before CI does
pytest -q --no-cov                  # full suite, not just the files you touched
```

If a check fails and the failure is **not** in a file your change touched,
confirm it's pre-existing before ignoring it:

```bash
git show origin/main:<path/to/failing/file>   # does the same failure already exist on main?
```

If the failure already exists on `origin/main`, it is not your bug — note it
in the PR description and move on. If it does not exist on `main`, it is a
regression from your own change — fix it before opening/leaving the PR open.

## Branch Workflow — Always Use Feature Branches

Never push directly to `main`. Always:

```bash
git checkout main && git reset --hard origin/main
git checkout -b cc/<short-description>    # cc/ prefix = Claude Code
# ... do work, commit ...
git push -u origin cc/<short-description>
gh pr create --title "..." --body "..."
gh pr merge <number> --squash --delete-branch --auto
```

Auto-merge is enabled repo-wide. Request it right when you open the PR — do
not wait around watching CI yourself. GitHub merges automatically once every
required check passes, so nobody has to remember to come back and click
merge. If a check fails, auto-merge just never fires; fix the check and the
same `--auto` request still applies once you push again.

## Branch Naming Convention

| Agent | Prefix | Example |
|-------|--------|---------|
| Claude Code | `cc/` | `cc/entitlement-gate-fix` |
| Codex | `codex/` | `codex/workflow-cleanup` |

This identifies ownership instantly when multiple PRs are open.

## Repo Ownership Split

Primary boundary — avoids overlap entirely:

| Repo | Primary agent |
|------|--------------|
| `mozaiks` (OSS framework) | Claude Code |
| `mozaiks-app` (hosted product) | Codex |

Both agents may touch either repo when needed, but this split should be the
default assignment. When both are working in the same repo simultaneously, the
branch workflow and the open PR check are the coordination mechanism.

## If PRs Conflict at Merge Time

The failing PR must rebase onto main after the winning PR lands:

```bash
git fetch origin
git rebase origin/main
git push --force-with-lease
```

This is expected behavior — not an error. The branch workflow makes it safe.

## A Repo-Wide CI Failure on `main` Blocks Everyone — Fix It First

If a check fails identically across multiple unrelated PRs (same file/line in
every failure), it is very likely already broken on `origin/main` itself and
now silently blocks every PR branched after it landed. Treat this as the
highest-priority fix: open a small, isolated hotfix PR against `main` for that
issue alone before continuing other work, since every other agent is blocked
by it too until it's fixed.
