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

If an open PR touches the same files you need: wait for it to merge, or branch
off that PR's branch if it is a hard dependency.

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

Auto-merge fires automatically once CI passes. No human action needed.

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
