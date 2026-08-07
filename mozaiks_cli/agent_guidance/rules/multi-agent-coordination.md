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

## Branch Cleanup

Feature branches are temporary work queues, not permanent project state. Always
merge with `--delete-branch` so cleanup happens automatically:

```bash
gh pr merge <number> --squash --delete-branch --auto
```

Two structural safety nets should also be in place at the repo level so cleanup
does not depend on every agent remembering `--delete-branch`:

1. Enable **"Automatically delete head branches"** in repo settings (or via
   `gh api -X PATCH repos/<org>/<repo> -f delete_branch_on_merge=true`). This
   deletes a branch the instant its PR merges, regardless of how it was merged.
2. Add a scheduled cleanup Action (e.g. weekly) that sweeps branches whose PR
   was closed without merging — those are not covered by delete-on-merge.
   Give it a grace period (a few days) before deleting a closed branch, and
   leave branches with no PR history at all for manual review.

If you ever need to audit branches by hand, do NOT rely on
`git branch -r --merged origin/main` if the repo squash-merges — squash-merged
branches never show up as git-ancestors of `main`, so that command will miss
almost all of them. Cross-reference against PR history directly instead:

```bash
git fetch origin --prune
gh pr list --state all --limit 300 --json headRefName,state,number,updatedAt
# delete only remote branches whose latest PR is MERGED, or CLOSED well past
# a grace period, and that are not main or an active in-flight branch
```

Do not delete a branch tied to an open PR or with no PR history at all —
inspect it first. Left unchecked, stale branches make it hard to tell which
work is real and which is abandoned, especially with multiple agents pushing
branches concurrently.

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
