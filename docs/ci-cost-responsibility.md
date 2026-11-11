# CI Cost Responsibility

Read this checklist before a push, workflow dispatch, rerun, or change to CI,
Docker/build scripts, runner selection, schedules, matrices, artifacts, or
Linux-backed validation. It supplements the existing coordination and review
rules; it does not replace required tests, security checks, or merge authority.

## Repository Baseline (2026-11-11)

This repository is public. Standard GitHub-hosted Linux execution can have no
net runner charge; that does not make private consumers, larger runners,
artifacts, or external services free. Do not copy private billing evidence here.

At main `ee6f32ac`, `.github/workflows/ci.yml` runs on PRs and main pushes,
including drafts. Its eight test shards multiply runner-minutes, and there is
no draft or `full-ci` gate. PR #979 proposes draft gating but is not active until
merged and reverified. Do not add a label expecting unmerged behavior.

Main protection currently requires `test`, `lint`, `frontend`, `secret-scan`,
`dco`, `package`, and `infra-build`, with the branch up to date. Preserve real
shard execution and coverage aggregation behind `test`. Re-read workflows and
settings before relying on this dated snapshot.

Existing PR volume, review, and cost rules remain canonical in
[the shared engineering contract](agent-engineering-contract.md#pull-request-volume-review-and-ci-cost).
The [pre-launch infrastructure restriction](../AGENTS.md#no-paid-infrastructure-until-launch)
also remains in force; this checklist does not authorize paid provisioning.

## Before Spending Runner Time

1. Identify the repository, billing owner, runner class, and trigger. Linux is
   an operating system, not a price tier. Standard GitHub-hosted runners in a
   public repository can be free; private-repository usage can consume an
   included allowance and then become billable. Larger runners, artifact/cache
   storage, cloud builders, and external APIs can have separate charges.
2. Read the workflow at the actual ref, including reusable/called workflows.
   Count every job and matrix leg across all workflows the push can trigger,
   including duplicate push/PR events, preview deployments, and downstream
   builds. Do not estimate from the slowest job or wall-clock duration alone.
3. Reuse recent comparable run/job timings to estimate total runner-minutes
   and likely cost using current rates and remaining allowance when available.
   Label unknown billing data as unknown, never as zero. Keep private billing
   details out of public issues, PRs, logs, and repositories.
4. Use the operator's existing task authorization and spending limits. Reasonable
   necessary validation already authorized does not require a new arbitrary cap
   for every run. New paid services, larger runners, recurring jobs, broad
   matrices, or materially more expensive validation need explicit direction.
   When optional expensive work has no clear authorization, finish local checks
   and report the cost/verification gap instead of starting it speculatively.
5. Check open PR ownership and the exact remote head/checks before publishing.
   Use the existing task PR, locally validate a coherent batch, then push once
   per useful review checkpoint. A draft PR or documentation-only commit does
   not by itself suppress Actions. Avoid empty commits as CI probes or locks.
6. Set a bounded plan: which checks, expected duration/job count, evidence
   needed, and when to stop. Inspect a failure before any retry. Never loop
   reruns or repeat an unchanged deterministic failure; use a targeted failed-job
   retry only for a diagnosed transient problem. A billing block is a stop, not
   a reason to retry or switch accounts/runners to bypass it.

## Local First, Required Checks Last

- Iterate with focused offline tests, lint, and builds in an isolated worktree
  using the repository's supported environment. Then run the full local checks
  required by repository policy before publishing. Report missing dependencies,
  stale pins, or unsupported OS behavior rather than modifying a shared venv,
  live dev service, credentials, or another agent's running work.
- Local Docker/WSL/Linux is appropriate only when already available and suitable.
  Verify whether the Docker context or build endpoint is remote and billable.
  Self-hosted runners still have compute, maintenance, and security costs; do not
  provision one, route untrusted PR code to one, or expose secrets to save minutes.
- Read-only inspection of runs/logs is preferable to another execution. Reuse
  valid evidence for the same code and environment, but never reuse a stale
  commit's result as proof for the final head.
- Draft gating or a `full-ci` label is usable only if the checked-in workflow
  actually implements it. Before merge, all required work must run and pass on
  the final head under the existing review policy. Skipped draft jobs, cancelled
  runs, missing checks, and a green aggregate hiding skipped work are not proof.
- Do not use `[skip ci]`, workflow-level path exclusions, fake success,
  `continue-on-error`, weaker coverage thresholds, removed security checks, or
  branch-protection changes to avoid a bill. Cost savings must preserve the
  required check contract and the tests behind it.

## When Changing Workflows

Keep workflow optimizations in their owning reviewed change. This instruction
file does not activate an unmerged cost-control PR.

- Prefer existing cache mechanisms keyed to lockfiles/tool versions; keep secret
  material out of caches. Retain only needed artifacts for the shortest useful
  period. Storage costs are separate from execution minutes.
- Bound jobs with appropriate timeouts and justify added matrix legs, new
  schedules, large runners, and retries. Check duplicate consumer/build jobs
  before adding another one. Parallelism reduces elapsed time, not necessarily
  billed runner-minutes.
- Cancel only superseded CI for the same PR/ref through a correctly scoped
  concurrency group. Do not cancel a deployment, release, financial operation,
  another agent's run, or the final evidence under review merely to save money.
- Validate workflow syntax and event behavior, including draft, ready-for-review,
  push, schedule, dispatch, failure, and skipped-dependency cases as applicable.
  A required check must still report truthfully; change its gating only through
  a reviewed CI design that preserves complete final-head validation.

## Budgets Are Separate From Instructions

Agent instructions reduce waste; they cannot guarantee no future bill. Azure
Cost Management budgets and ordinary budget notifications alert on usage and
are not an automatic stop. GitHub budgets must be inspected for the correct
account/product/SKU scope and an explicit stop-usage setting where supported.
An alert-only budget, timeout, or concurrency limit is not a monthly hard cap.

Read current billing/enforcement settings when access permits. Report the exact
scope, whether stop-usage is enabled, and unverified coverage. Do not change
budgets, billing links, payment methods, runner access, or security settings
without the operator's authorization. Explain that a real stop can block required
checks and releases. External cloud/API charges may remain outside its scope.

## Completion Evidence

Report the exact commit, local checks actually run, hosted run URLs and status,
any extra dispatch/retry and why it was necessary, and observed job-minutes when
available. Distinguish estimates from billed usage. State unfinished or skipped
validation and whether cost controls were only documented or actually enforced.
