# Multi-Agent Coordination Rules

Use these rules whenever you are about to start work, push, or merge in this repo.
Multiple coding agents may operate simultaneously on the same workspace.
Without coordination they will stomp on each other's in-flight changes.

## Before Starting Any Task

```bash
git fetch origin
gh pr list --state open           # see what other agents have in flight
git log origin/main --oneline -5  # see what recently landed
```

If an open PR touches the same files you need:
- If it is nearly done (CI passing), wait for it to merge first.
- If it is a hard dependency, branch off that PR's branch instead of main.

## Branch Workflow — Always Use Feature Branches

Never push directly to `main`. Always:

```bash
git checkout main && git reset --hard origin/main
git checkout -b cc/<short-description>      # cc/ = Claude Code
#              codex/<short-description>    # codex/ = Codex
# ... do work, commit ...
git push -u origin cc/<short-description>
gh pr create --title "..." --body "..."
gh pr merge <number> --squash --delete-branch --auto
```

Auto-merge fires once required CI checks pass. No human action needed.

## Branch Naming Convention

| Agent | Prefix | Example |
|-------|--------|---------|
| Claude Code | `cc/` | `cc/add-payment-module` |
| Codex | `codex/` | `codex/fix-wallet-service` |

This makes it immediately clear which agent owns which branch when multiple
PRs are open at the same time.

## If PRs Conflict at Merge Time

The second PR must rebase onto main after the first one lands:

```bash
git fetch origin
git rebase origin/main
git push --force-with-lease
```

This is expected behavior — not an error. The branch workflow makes it safe.
