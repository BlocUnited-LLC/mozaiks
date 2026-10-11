# Pull Request Review and Merge Policy

This rule supplements `multi-agent-coordination.md`. It governs how agents
review and merge existing PRs; it does not change application code, CI
commands, branch protection, or ownership boundaries. If this rule conflicts
with `AGENTS.md`, `AGENTS.md` wins.

## Fast path for low-risk PRs from another author

An agent may approve and request squash auto-merge for a pull request it did
not author, such as a Dependabot update, after reviewing the diff, when every
condition below is true. Pull requests pushed by the agents' own account
cannot be approved by that account; they take the manual-review path.

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
Request squash auto-merge immediately after approval, bound to the head you
reviewed so a later push cannot ride the approval:

```powershell
gh pr review <number> --approve --body "Reviewed scope and required checks; no unresolved risk identified."
gh pr merge <number> --squash --auto --match-head-commit <reviewed head sha>
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

Agents do not change branch protection (see Repository Settings And Merge
Authority in `docs/agent-engineering-contract.md`). When the sole author
cannot approve a PR that an independent reviewer cleared at an exact head,
report that head SHA, the review, and the green required checks, and stop.
The maintainer decides whether to use the exception.

If the maintainer uses it, the window must stay as short as one command and
must not merge anything else:

1. Disable every armed auto-merge in the repository first
   (`gh pr list --state open --json number,autoMergeRequest`, then
   `gh pr merge <n> --disable-auto` for each). An armed auto-merge fires the
   moment the review gate drops; that is how an unrelated PR once merged
   unreviewed.
2. In one line: set the required approving review count to zero, merge the
   reviewed head directly with
   `gh pr merge <number> --squash --match-head-commit <reviewed head sha>`
   (not `--auto`), and restore the count to one.
3. Verify the restored state with
   `gh api repos/<owner>/<repo>/branches/main/protection` and record the PR
   number, merge SHA, and restored protection state.

Never use an administrator merge as a substitute.

## Ownership and isolation

Reviewing or merging an existing PR does not authorize changes to another
agent's branch. Work on follow-up fixes in a fresh worktree from
`origin/main`, claim the files through a draft PR, and do not branch from an
open PR. A merge review must remain read-only unless the requester explicitly
asks for a follow-up implementation.
