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

After confirming a PR has merged, use the repository cleanup report before
removing old agent worktrees:

```bash
python scripts/prune_agent_worktrees.py
```

This is a dry run. Review the complete report before using `--apply`. Never
delete dirty, unpushed, or detached worktrees based only on their age or on a
remote branch being deleted. The script is intentionally conservative and
keeps work when GitHub state cannot be verified.

When creating or editing a PR, send real Markdown to GitHub. Do not use a
quoted shell argument containing `\\n`; those characters render literally.
Use `gh pr create --body-file` with a temporary Markdown file, or a
shell-native literal here-string, and verify with `gh pr view <number>`.

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
# ... do work, commit -s ...
git push -u origin cc/<short-description>
gh pr create --title "..." --body "..."
gh pr merge <number> --squash --delete-branch --auto
```

**Every commit must be signed off (DCO) — always use `git commit -s`, never
plain `git commit -m`.** This repo is under the Developer Certificate of
Origin (see [DCO.md](../../DCO.md)); a commit without a `Signed-off-by:`
trailer fails the `dco` check. That check is not yet in the `main` branch
protection required-checks list, so `--auto` can still merge a PR with a
failing `dco` check — do not treat a green auto-merge as proof you signed off
correctly. If you forgot on the last commit, fix it before pushing (or before
leaving a PR open) with `git commit --amend -s --no-edit && git push --force-with-lease`;
for several unsigned commits use `git rebase --signoff origin/main` first.

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

## Who Owns What

**No repo belongs to one agent.** Claude Code and Codex both work in both repos,
often at the same time, and work regularly spans the two. Do not defer a task
because you think another agent owns the repo it lives in, and do not assume a
repo is quiet because it is "theirs".

Where a change belongs is decided by the layering rule -- generic mechanism in
`mozaiks`, hosted product specifics in `mozaiks-app`. That is a question about
the code, not about which agent may write it.

Ownership is per **task**, and it is claimed, not assigned.

## Claiming Work

Several agents run concurrently, and more than one may be the same tool -- two
Claude Code sessions both push `cc/` branches, so the prefix identifies the tool,
not the worker. Naming alone cannot prevent a collision. Claiming can.

Before writing code:

```bash
git fetch origin
gh pr list --state open                       # what is in flight
gh pr list --search "<file or subsystem>"     # has someone claimed this?
git log origin/main --oneline -10             # what just landed
```

Search for the **files** you intend to change, not just the topic. A duplicate
pin-bump PR was nearly opened this way: the work was already in flight under a
name that did not mention the file it touched.

Then claim it before you build it:

- push the branch and open a **draft PR immediately**, with a title naming the
  files or subsystem. An empty draft PR is a cheap lock other agents can see.
- if an open PR already covers your task, **do not open a second one**. Add your
  findings as a comment on theirs -- evidence on an existing PR is worth more
  than a competing one.

## Isolation

**Always work in a worktree.** Agents share checkouts; a `git checkout` in a
shared tree silently switches another agent's branch and sweeps their
uncommitted edits into your commit.

```bash
git worktree add .local/worktrees/<task> origin/main -b cc/<task>
```

Do not run `npm install` inside a worktree on Windows. Deleting a worktree
follows `node_modules` junctions and destroys the **main** checkout's packages;
`find -type l` does not detect them. Verify with PowerShell before removing:

```powershell
Get-ChildItem -Path $wt -Recurse -Force | Where-Object { $_.LinkType }
```

## The Shared Runtime Is Not Protected By Any Of This

Branch rules cover the repo. They do not cover the one dev stack every agent
shares: a single venv, a single Mongo, ports 3000/8000.

- `pip install -r requirements.txt` in `mozaiks-app` replaces the OSS package
  for **every** agent. It silently reverted another agent's editable install
  mid-session and cost most of a day debugging fixes that were never loaded.
- restarting the stack restarts it for everyone.
- seeded data (wallets, chat sessions) is shared, and a concurrent build raises
  global counts -- attribute results to your own `chat_id`, never to a total.

If you change the shared environment, say so in your PR or a comment. If you are
verifying something, first prove the running stack contains your change rather
than assuming a restart was enough.

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
