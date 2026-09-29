# Pull Request Review and Merge Policy

This rule supplements `multi-agent-coordination.md`. It governs how agents
review and merge existing PRs; it does not change application code, CI
commands, branch protection, or ownership boundaries. If this rule conflicts
with `AGENTS.md`, `AGENTS.md` wins.

## Fast path for low-risk PRs

An agent may approve and request squash auto-merge after reviewing the diff
when every condition below is true:

- The PR is ready for review, mergeable, and has no unresolved conflicts.
- Required checks are green or GitHub explicitly considers a skipped check
  satisfied. Required checks include tests, lint, security/dependency scans,
  packaging, and any applicable frontend, infrastructure, or visual checks.
- The diff is narrow and low-risk: documentation-only, or a focused patch
  dependency update with no code or lockfile behavior beyond the update.
- The PR has no unresolved review comments and does not depend on an open PR's
  unmerged branch.
- The PR does not change runtime behavior, authentication or authorization,
  persistence, migrations, schemas, workflows, generated-app contracts,
  security policy, or release infrastructure.
- The PR body does not request manual review or say not to auto-merge.

Before merging, inspect the changed-file list and the check summary directly:

```powershell
gh pr view <number> --json files,isDraft,mergeable,mergeStateStatus,reviewDecision
gh pr checks <number>
```

Use an approval body that records the scope reviewed and the checks relied on.
Request squash auto-merge immediately after approval:

```powershell
gh pr review <number> --approve --body "Reviewed scope and required checks; no unresolved risk identified."
gh pr merge <number> --squash --auto
```

After the merge, record the PR number and final merge SHA in the handoff.

## Manual-review path

Do not auto-merge when any of these apply:

- The PR changes runtime, generator, workflow, schema, persistence, auth,
  security, migrations, generated artifacts, or release behavior.
- The PR contains a grouped frontend or backend dependency update with several
  behavioral packages; review the affected surface and visual/security checks.
- Any required check fails, is missing, or has an unexplained skip.
- The PR is draft, has conflicts, has unresolved comments, or has an explicit
  no-auto-merge instruction.
- The change overlaps another agent's open PR or would require editing,
  rebasing, or force-pushing another agent's branch.

For these PRs, report the blocker and leave the PR open. Do not bypass a failed
check or infer approval from a green subset of checks.

## Sole-maintainer exception

When the sole author cannot approve an otherwise fully verified PR, preserve
all required checks and direct-push protection. Temporarily set only the
required approving review count to zero, request squash auto-merge, wait for
the merge to complete, and immediately restore the approval count to one.
Do not use an administrator merge as a substitute for this procedure.

Verify the protection state after restoration:

```powershell
gh api repos/<owner>/<repo>/branches/main/protection
```

Record the PR number, temporary exception, final merge SHA, and restored
protection state. If the agent lacks permission to update branch protection,
stop and report the blocker rather than bypassing the rule.

## Ownership and isolation

Reviewing or merging an existing PR does not authorize changes to another
agent's branch. Work on follow-up fixes in a fresh worktree from
`origin/main`, claim the files through a draft PR, and do not branch from an
open PR. A merge review must remain read-only unless the requester explicitly
asks for a follow-up implementation.
