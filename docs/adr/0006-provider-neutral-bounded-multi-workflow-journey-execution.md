# ADR 0006: Bounded Multi-Workflow Journey Execution

Date: 2026-08-25

Status: Proposed

## Context

Mozaiks can execute one workflow through `RunRequest`, `RunResult`, and
`OrchestrationPort` in `mozaiksai/core/ports/orchestration.py`.
`AG2OrchestrationAdapter` in
`mozaiksai/core/adapters/ag2_orchestration.py` implements that workflow-level
port. User input and resume flow through
`WorkflowBridgeMixin.handle_user_input_from_api` in
`mozaiksai/core/transport/workflow_bridge.py`.

`SessionRouter.advance_journey_after_run_complete` in
`mozaiksai/core/session/router.py` and
`JourneyOrchestrator.handle_run_complete` in
`mozaiksai/core/workflow/pack/journey_orchestrator.py` can advance a declared
sequence and spawn its next workflows as process-local background tasks. The
canonical `build` sequence in
`factory_app/workflows/extended_orchestration/extension_registry.json` is:

```text
app_type_selector
→ ValueEngine
→ ThemeCapture
→ coding_journey_selector
→ database_setup_selector
→ DesignDocs
→ SubscriptionContractDesigner
→ AgentGenerator
→ AppGenerator
→ app_review / AppReview
```

No public contract owns that complete objective. Current callers cannot await
one durable execution outcome and its accounting settlement, enumerate every
spawned workflow run, atomically bound shared token use, or durably cancel
future advancement. This prevents a raw-prompt evaluator from truthfully
distinguishing “ValueEngine completed” from “the complete build journey
completed,” and it leaves the same production operator-control gap.

Relevant current boundaries are:

- `SessionState`, `JourneyAdvanceDecision`, and current journey fields in
  `mozaiksai/core/session/model.py`;
- `SessionStateStore` in `mozaiksai/core/session/persistence.py`;
- launch types and functions in `mozaiksai/core/session/launcher.py`;
- chat lifecycle persistence in
  `mozaiksai/core/data/persistence/persistence_manager.py`;
- AppGenerator's concurrent `app_build_tasks` declaration in
  `factory_app/workflows/AppGenerator/extended_orchestration/task_batches.yaml`;
- task dispatch in `mozaiksai/core/workflow/task_batches.py` and
  `AG2TaskBatchRunner` in
  `mozaiksai/core/adapters/ag2_task_batch_runner.py`;
- post-call usage events in `mozaiksai/core/usage/middleware.py` and
  `mozaiksai/core/tokens/manager.py`;
- `RuntimeUsageLedger` in `mozaiksai/core/usage/ledger.py`;
- the alert-only AG2 token watchdog in
  `mozaiksai/core/usage/watchdog.py`; and
- generated artifact-root resolution through
  `MOZAIKS_GENERATED_ARTIFACTS_PATH` in AgentGenerator and AppGenerator tools.

## Decision

Introduce a distinct provider-neutral `JourneyExecutionPort` above
`OrchestrationPort`.

`OrchestrationPort` remains responsible for one workflow execution. It gains
only typed journey scope on its existing run/resume requests. It does not gain
journey start, listing, waiting, artifact retention, or cross-workflow
cancellation methods.

`JourneyExecutionPort` owns one complete declared journey from durable start to
one durable execution outcome and a separately settling accounting record. It
uses existing routing and workflow execution; it does not invoke each workflow
through a second scheduler.

This ADR approves the contract direction only. It does not accept the ADR,
implement an interface, change workflow behavior, or authorize model spend.

## Identity

The canonical identities are:

| Identity | Meaning |
|---|---|
| `workflow_sequence_id` | Declarative sequence definition, such as `build`. |
| `journey_id` | One complete user objective executing a sequence. |
| `workflow_run_id` | One workflow execution within the journey. |
| `parent_workflow_run_id` | Optional link to the workflow run that spawned a child through advancement, repair, or decomposition. |
| `chat_id` | Workflow conversation/session identity; not a journey or run identity. |
| provider run ID | Adapter-private AG2/provider identity; never a public journey identity. |
| `invocation_id` | One provider/model call used for reservation reconciliation. |

`journey_id` and `workflow_run_id` are server-generated, opaque, immutable, and
tenant-scoped. They are distinct from `app_id`, subscription/customer usage
identity, `chat_id`, and AG2 identifiers.

Current routing uses `journey_id`/`journey_key` for sequence selection and
`journey_instance_id` for an instance. A future pre-1.0 implementation replaces
that ambiguity directly: `workflow_sequence_id` names the definition and
`journey_id` names the execution. It must not retain aliases or dual-write
fields.

## Illustrative Public Contract

These types illustrate required semantics, not production implementation:

```python
class JourneyExecutionPort(Protocol):
    async def start(
        self, request: JourneyStartRequest
    ) -> JourneyExecutionHandle: ...

    async def snapshot(
        self, journey_id: str, access: ExecutionAccess
    ) -> JourneySnapshot: ...

    async def list_workflow_runs(
        self, journey_id: str, access: ExecutionAccess
    ) -> tuple[WorkflowRunSnapshot, ...]: ...

    async def wait_terminal(
        self,
        journey_id: str,
        access: ExecutionAccess,
        timeout_seconds: float | None = None,
    ) -> JourneyExecutionOutcome: ...

    async def terminal_outcome(
        self, journey_id: str, access: ExecutionAccess
    ) -> JourneyExecutionOutcome | None: ...

    async def wait_settled(
        self,
        journey_id: str,
        access: ExecutionAccess,
        timeout_seconds: float | None = None,
    ) -> JourneySettledResult: ...

    async def settled_result(
        self, journey_id: str, access: ExecutionAccess
    ) -> JourneySettledResult | None: ...

    async def cancel(
        self,
        journey_id: str,
        access: ExecutionAccess,
        reason_code: str = "caller_requested",
    ) -> JourneySnapshot: ...
```

`JourneyExecutionHandle` wraps the same authorized operations. Losing a local
handle does not cancel durable work. A timeout while waiting stops only the
waiter. `terminal_outcome` returns `None` before execution termination and never
fabricates success from a completed child workflow. `wait_terminal` waits only
for execution termination; callers that require final usage call `wait_settled`.

Implementations advertise a versioned set of supported policy capabilities.
Examples include durable lifecycle, deadline enforcement, cooperative
cancellation, durable dispatch, and shared token reservation. Required
capabilities are derived deterministically from the requested policy and the
pinned journey definition; callers cannot weaken them through arbitrary
metadata. A start request that requires a capability the selected implementation
does not advertise fails closed before work starts with the stable, versioned
`journey_execution_capability_unavailable` reason and bounded missing-capability
details.

Phase 1 does not advertise shared token reservation and therefore must not claim
token-bounded execution merely because it persists `max_total_tokens`. Public
live-model journey start remains disabled until both the lifecycle phase and the
shared token-reservation phase are implemented and proven. The raw-prompt live
evaluator requires the bounded public journey contract and cannot use
`legacy_unbounded` mode.

The versioned, immutable `JourneyExecutionPolicy` contains:

```text
schema_version: "mozaiks.journey_execution_policy.v1"
deadline_utc: timezone-aware absolute UTC datetime
max_total_tokens: positive integer
max_workflow_run_starts: optional positive integer
max_task_attempt_starts: optional positive integer
max_provider_retry_attempts: optional non-negative integer
max_structured_output_correction_attempts: optional non-negative integer
max_workflow_repair_starts: optional non-negative integer
max_app_refinement_starts: optional non-negative integer
cancellation_mode: "cooperative"
cancellation_grace_seconds: positive integer
accounting_settlement_grace_seconds: positive integer
partial_artifact_policy:
  on_non_success: "delete" | "retain_until"
  retain_until_utc: required for "retain_until"
```

A versioned `JourneyScope` contains `journey_id`, `workflow_sequence_id`, pinned
sequence version, `workflow_run_id`, optional `parent_workflow_run_id`, immutable
creation-scope reference, policy reference/version, stage identity, and the
process-local monotonic deadline when derived. It is an explicit typed field
such as `RunRequest.journey_scope`, `ResumeRequest.journey_scope`, and the
equivalent task-batch request field. It must not be hidden in `RunRequest.extra`,
workflow prompt text, arbitrary context metadata, or AG2 objects.

`JourneyStartRequest` contains `ExecutionAccess`, an idempotency key,
`workflow_sequence_id`, its declared entry transition/entrypoint, required typed
route choices, initial user message where supported, and the policy. Before
persisting `pending`, start resolves the named sequence from the authoritative
registry, validates that the entry transition and every route choice are
declared by that sequence, and pins an immutable sequence version or
content-addressed digest. The persisted resolved sequence is authoritative for
the journey lifetime; a later registry edit cannot alter an in-progress
journey. The canonical idempotency request hash includes the pinned sequence
version, validated route choices, entry transition, immutable creation scope,
and complete policy. Start then uses the existing `SessionRouter` and launcher;
no workflow, task, or model call starts before persistence succeeds.

## Observation Contract

`JourneySnapshot` exposes:

- `journey_id`, `workflow_sequence_id`, pinned sequence version, lifecycle
  state, terminal reason, and `accounting_status`;
- current stage and ordered constituent workflow runs, including parent links;
- workflow/chat/run identities and stage outcomes;
- timestamps and absolute deadline;
- maximum token ceiling, estimated prompt tokens, outstanding reservations,
  provider-observed actual usage, and reconciled committed usage;
- retry/repair counts where available;
- cancellation request, reason, `cancellation_requested_at`,
  `cancellation_deadline_utc`, and settlement metadata;
- sanitized diagnostics and authorization-scoped partial artifact references;
  and
- a monotonically increasing record version.

`WorkflowRunSnapshot` contains its workflow and parent identities, sequence
position, workflow name, chat/session identifiers, outcome, attempts,
timestamps, sanitized diagnostics, and artifact references. No public snapshot
exposes credentials, raw prompts/model output, provider request bodies, private
stack traces, AG2 objects, or filesystem paths.

Ordering is sequence position, parallel-member ordinal, creation time, then
stable ID. Each snapshot is self-consistent at one committed record version;
the contract does not require a particular database or linearizable streaming
between snapshots.

Execution outcome and accounting settlement are distinct contracts:

- `JourneyExecutionOutcome` is created when an authoritative terminal lifecycle
  transition wins. Its execution state, terminal reason, ordered workflow
  outcomes, diagnostics, and artifact disposition are immutable immediately.
- `accounting_status` is `pending`, `settled`, or `settlement_failed`. Snapshot
  accounting fields and record version may change while it is `pending` as
  already-transmitted calls and outstanding reservations reconcile. Those
  updates cannot change execution state or reason, resume work, promote an
  artifact, or advance the journey.
- `JourneySettledResult` combines the immutable execution outcome with final
  accounting. It is published once accounting becomes `settled` or
  `settlement_failed` and is immutable thereafter. `settled_result` returns
  `None` while accounting is pending.

Evaluation may record the execution outcome when `wait_terminal` returns, but a
final evaluation score and budget/usage verdict waits for `wait_settled`. A
settlement failure is explicit evaluation evidence, never silently treated as
zero usage or a successful fully-accounted run.

## Lifecycle And Race Precedence

States are:

```text
pending
running
cancelling
completed
failed
timed_out
budget_exceeded
cancelled
```

Legal transitions are:

- `pending → running | cancelling | failed | timed_out | budget_exceeded`;
- `running → cancelling | completed | failed | timed_out | budget_exceeded`;
- `cancelling → cancelled`; and
- no transitions out of terminal states.

Terminal states are `completed`, `failed`, `timed_out`, `budget_exceeded`, and
`cancelled`. Workflow pauses and review/user-input waits leave the journey
`running` and appear in the current workflow-run snapshot.

State transitions use compare-and-set against allowed source state and record
version. Precedence is:

1. An existing terminal state is immutable.
2. Existing `cancelling` owns the stop and transitions to `cancelled`; later timeout,
   budget, failure, or completion cannot replace it.
3. Within one checkpoint transaction, an expired deadline is evaluated before
   token availability, so simultaneous expiry becomes `timed_out`.
4. Otherwise a requested reservation that would exceed the ceiling becomes
   `budget_exceeded` before provider work starts. Failure to form a conservative
   reservation at all becomes `failed` with `token_reservation_unavailable`.
5. Otherwise the first successful cancellation, failure, or completion
   compare-and-set wins. Completion rechecks every stop condition atomically.

Terminal reason is a separate stable code, such as `journey_completed`,
`caller_requested`, `deadline_expired`, `token_budget_exhausted`,
`token_reservation_unavailable`, `workflow_failed`,
`lifecycle_persistence_unavailable`, or `orphan_recovery_unsafe`.

Cancellation reason codes are a bounded, versioned enum or registry.
`caller_requested` is the normal public cancellation code. An optional human
explanation is separate bounded, sanitized metadata; arbitrary user text cannot
become a lifecycle reason, metric label, or raw diagnostic. Unsupported
cancellation reason codes fail request validation before lifecycle state
changes.

Lifecycle states, accounting states, reservation states, terminal reason codes,
and snapshot/outcome/settled-result schemas are versioned public compatibility
surfaces. Each public record carries its schema version; an incompatible enum or
field change requires a new schema version. Clients preserve an unknown reason
code as an opaque value and use the versioned lifecycle state for control flow;
an unknown code must never fall back to success, automatic retry, resume, or
artifact promotion.

Repeated cancellation is idempotent. A terminal journey rejects resume and
cannot auto-advance.

Duplicate start with the same immutable creation scope, idempotency key, and
canonical request hash returns the existing handle. Reusing a key with changed
inputs fails with `idempotency_conflict`. Resume is keyed by `journey_id`,
`workflow_run_id`, and its pending input/checkpoint; replay returns the recorded
outcome instead of starting another attempt.

## Deadline Semantics

`deadline_utc` is persisted and authoritative across pauses and restarts. Each
process derives an unpersisted monotonic deadline from the remaining UTC
interval. Local work uses the earlier indication of expiry, so wall-clock
adjustment cannot extend a running attempt. Workflow/task/tool timeouts are
clamped to remaining journey time.

The deadline propagates through the initial run, user-input resume,
structured-output correction, task batches, repair/refinement routes,
auto-spawned workflows, background tasks, and restarted workers.

User-input and human-review pauses remain `running`; the absolute deadline keeps
advancing and resume never extends it automatically. Production callers must
select a deadline that accounts for the intended review window. A user returning
at or after expiry receives the immutable `timed_out` outcome instead of
resuming the paused workflow.

If expiry occurs during an in-flight provider call, cancellation is requested
when supported. An uninterruptible call may finish and its usage is reconciled,
but its result is quarantined: it cannot change context, retry, repair, write or
promote artifacts, export, or advance. A worker starting or resuming at or after
the persisted deadline transitions to `timed_out` before loading an agent or
dispatching work.

## Token Budget And Reservation Semantics

The contract distinguishes journey counters from per-invocation reservations.
Each reservation has a versioned lifecycle:

```text
reserved
transmitted
reconciled
released_untransmitted
expired_conservatively
```

`reserved` records `journey_id`, unique `invocation_id`, conservative amount,
owner/lease identity, lease expiry, and creation version before external
transmission. Duplicate reservation of the same invocation returns the same
record and amount or fails an input-hash conflict; it never reserves twice.
Transmission is made durable by atomically changing the reservation to
`transmitted` before the provider request can leave the adapter boundary.

A crash while still `reserved` proves no request was transmitted: recovery
moves it to `released_untransmitted` immediately or when its lease expires,
without charging reconciled usage. The record remains durable for idempotency; a
reservation never disappears because its worker dies. A crash after
`transmitted` cannot release the reservation. On lease expiry it becomes
`expired_conservatively` and its full reserved amount is committed. If
authoritative provider usage arrives before accounting settlement finalizes,
idempotent reconciliation replaces that conservative amount with actual usage
and moves the reservation to `reconciled`, without double-counting. After
accounting becomes `settled` or `settlement_failed`, later signals are retained
only as sanitized discrepancy evidence and cannot mutate the immutable settled
result. Reservation state changes and the corresponding journey counters occur
in one atomic transaction or a provably equivalent consistency boundary.

Accounting cannot remain pending forever. A terminal execution persists an
`accounting_deadline_utc` derived from the policy settlement grace. At that
deadline, every still-`transmitted` reservation becomes
`expired_conservatively` and is charged fully. Accounting becomes `settled`
only when every invocation is authoritatively `reconciled` or proved
`released_untransmitted`; any conservative expiry or known missing/malformed
usage produces `settlement_failed`. Either final accounting status may be
reached early when all reservations have a final disposition.

The usage fields are:

- `estimated_prompt_tokens`: conservative input estimate before a call;
- `reserved_tokens`: outstanding worst-case capacity;
- `observed_actual_tokens`: provider-reported post-call usage; and
- `reconciled_tokens`: authoritative committed usage.

Post-call accounting alone is rejected as a hard bound because one call can
overshoot before usage is reported.

V1 chooses pre-call reservation. Before every model call, the adapter computes
a conservative prompt-token upper bound and adds the configured maximum output
tokens. The lifecycle store atomically permits the call only when:

```text
reconciled_tokens + outstanding_reserved_tokens + requested_reservation
    <= max_total_tokens
```

All concurrent workflows and AppGenerator task-batch workers contend on the
same journey reservation authority.

After a call, reported actual usage is committed and unused reservation is
released. Missing or malformed usage moves the reservation to
`expired_conservatively` and commits its full amount. Duplicate events cannot
double-count. Usage from an in-flight call that finishes after execution
termination is reconciled only while accounting is pending, and its output can
never progress the journey.

When no reliable provider-neutral estimator or configured maximum output exists,
the conservative fallback is fail-closed before request transmission with
sanitized `token_reservation_unavailable` diagnostics. Mozaiks must not use a
hopeful character ratio or silently fall back to post-call enforcement.

This is a no-start guarantee. It is a hard ceiling only when the adapter's input
bound is conservative and the provider honors its maximum output. If reported
actual usage exceeds its reservation before another terminal state wins,
Mozaiks records actual usage, transitions to `budget_exceeded`, starts nothing
further, and reports the bounded discrepancy rather than claiming no overshoot.
If execution is already terminal, the same accounting discrepancy is recorded
without changing its immutable outcome.

The existing `TokenUsageGuard` remains a separate wallet/commercial check.
`TokenMonitor` and `chat.token_budget_alert` remain advisory observability; they
are not reservation authority.

## Policy Counter Semantics

Every configured counter is journey-wide. A claimant atomically reserves and
increments the counter before starting the counted operation. Once claimed, an
attempt remains consumed even if its worker crashes; duplicate delivery of the
same stable operation ID returns the existing claim. Concurrent claims cannot
exceed the configured maximum.

| Policy field | Counted operation | Initial attempt |
|---|---|---|
| `max_workflow_run_starts` | Every new `workflow_run_id`, including initial, child, repair, or refinement workflows. A retry inside an existing run does not count again. | Counts. |
| `max_task_attempt_starts` | Every parallel task-batch member attempt. It does not also count as a workflow-run start unless it creates a new workflow run. | Counts for each member. |
| `max_provider_retry_attempts` | Retransmission of the same intended provider request after transport or transient-provider failure. Validation-driven correction is counted only by the correction counter. | Does not count. |
| `max_structured_output_correction_attempts` | Each additional model attempt requested because structured output failed validation. | Does not count. |
| `max_workflow_repair_starts` | Each repair route entered to repair a failed or invalid workflow result. A repair that creates a new workflow run also consumes a workflow-run start. | Does not count the original workflow. |
| `max_app_refinement_starts` | Each AppGenerator refinement/repair route entered for generated-app correction. If it creates a new workflow run or task attempt, those counters also apply. | Does not count the initial AppGenerator pass. |

Exhaustion terminates execution as `budget_exceeded` using the corresponding
stable reason:
`workflow_run_start_limit_exhausted`, `task_attempt_start_limit_exhausted`,
`provider_retry_limit_exhausted`,
`structured_output_correction_limit_exhausted`,
`workflow_repair_limit_exhausted`, or `app_refinement_limit_exhausted`. A single
attempt can intentionally consume multiple orthogonal counters; no combined
`max_retry_repair_attempts` alias is retained.

## Cost And Commercial Separation

Hard monetary ceilings are deferred. Existing pricing facilities may report an
estimate after token usage, and adapters may report provider-observed actual
cost, but post-call values cannot prevent an expensive call from starting.
Exact preflight would require authoritative versioned pricing and conservative
maximum-call cost for every provider behavior. A later schema and decision may
add that capability.

Journey budgets are operator/evaluation safety controls, independent of:

- subscription entitlements and monthly `usage_limits`;
- token-wallet customer balances;
- MozaiksPay billing;
- checkout, settlement, and fulfillment.

A commercially entitled/funded user may still exhaust the journey budget. A
journey may have budget remaining while a commercial entitlement denies the
action. Passing either check never implies passing the other, and journey
accounting does not mutate commercial balances or claim monthly quota
enforcement.

## Propagation And Cancellation

Typed scope propagates through:

| Boundary | Requirement |
|---|---|
| Initial launch | Launcher receives `JourneyScope` separately from workflow-authored context. |
| Workflow run/resume | `RunRequest` and `ResumeRequest` carry scope explicitly. |
| Model calls and structured correction | Check lifecycle, deadline, exact attempt counter, and reservation before every attempt. |
| Task batches | Every task/attempt inherits the same journey and its own workflow/stage identity. |
| Repair/refinement routes | Count and check before routing or starting more work. |
| Auto-advancement | Commit parent completion, unique child claim/ID, and durable dispatch intent atomically before external dispatch. |
| Background/restarted workers | Reload protected scope and policy; process-local task maps are never authority. |
| Usage, events, telemetry | Internal usage/events carry scoped IDs; external telemetry omits or one-way hashes identity. |
| Artifacts | Metadata and generated-root namespace carry `journey_id`; snapshots expose opaque scoped refs. |

Cancellation atomically persists `cancelling`, `cancellation_requested_at`, and
`cancellation_deadline_utc` before tasks are signalled. The cancellation
deadline is the request time plus the policy's bounded grace; the effective
stop/quarantine cutoff is the earlier of that value and `deadline_utc`. Required
checkpoints are before workflow start, provider call, retry/correction/repair,
task-batch dispatch, auto-advancement, safely interruptible persistence/export
side effects, and terminal completion.

Cancellation immediately prevents new work and advancement. Queued work stops;
active attempts are signalled and their outputs quarantined. Execution may
transition from `cancelling` to `cancelled` when every active attempt has stopped
or been durably quarantined, or when the effective stop/quarantine cutoff
expires. A provider that ignores cancellation, a crashed worker, an abandoned
reservation, or missing usage therefore cannot keep execution nonterminal
forever.

If caller cancellation has durably won before the journey deadline, later
journey-deadline expiry may shorten the remaining stop/quarantine wait but cannot
replace the outcome with `timed_out`; the terminal outcome is `cancelled`.
Accounting may remain `pending` after execution becomes `cancelled` and later
become `settled` or `settlement_failed` under its own finite accounting deadline.
Restarted workers can perform this cancellation and accounting settlement
idempotently from persisted attempts, reservations, and deadlines.

## Restart, Persistence, And Partial Artifacts

Journey records use a durable owner lease/heartbeat. A restarted worker first
checks terminal/cancelling state, absolute deadline, budget, and limits. It may
claim an expired lease and resume only when persisted workflow/chat/stage state
proves the operation idempotent; otherwise it fails with
`orphan_recovery_unsafe`. Process-local `_background_tasks` is not recovery
authority.

Auto-advancement uses a transaction or provably equivalent consistency boundary
that records the completed parent workflow run, unique child-stage claim,
preallocated child `workflow_run_id`, applicable counter claims, and durable
dispatch intent/outbox record. Only after that commit may a task or background
dispatcher deliver the intent. A crash after commit but before dispatch is
recoverable by replaying the intent; duplicate delivery is idempotent on
`workflow_run_id`.

Dispatch acknowledgement marks delivery state but does not delete the
authoritative intent before the child has durably claimed the same run ID.
Recovered dispatch rechecks terminal/cancelling state, deadline, and token
budget, and verifies the existing counter claims before execution; it never
increments them again. A stopped journey marks the intent durably suppressed
instead of dispatching it. Parent completion replay and advancement replay
return the existing child claim and cannot create another child. Process-local
background-task collections are never authoritative.

Authoritative lifecycle, reservation, stage, execution-outcome, and accounting
persistence is fail-closed. If state cannot be committed, no new work starts and
the runtime cannot claim durable success.

Artifacts are associated with exact `journey_id`:

- `completed`: canonical promoted artifacts follow normal lifecycle; temporary
  files, subprocesses, handles, provider sessions, and sandboxes are cleaned;
- `failed`, `timed_out`, `budget_exceeded`, or `cancelled`: transient resources
  are cleaned, while unpromoted artifacts follow `delete` or finite
  `retain_until` policy; and
- cleanup uses stable resource keys, is idempotent after restart, and cannot
  affect another journey or app.

Useful sanitized evaluation evidence need not be deleted. Credentials, provider
request bodies, secret-bearing logs, and unsanitized diagnostics are never
retained artifacts.

## Security And Tenancy

`ExecutionAccess` is the provider-neutral authorization boundary presented on
start and every later operation. At start, authorization resolves and persists
a versioned, immutable `ExecutionAccessScope` identifying the authoritative
owner/tenant and, where applicable, workspace and pre-app build scope. A raw
build therefore does not require an `app_id` that does not yet exist.

- Start, snapshot, listing, wait, result retrieval, and cancellation authorize
  against the immutable creation scope. Possession of `journey_id`, `chat_id`,
  or a later `app_id` is insufficient.
- A generated `app_id` may later be associated with the journey for artifact or
  runtime correlation, but it cannot replace, widen, or weaken creation scope.
- Every read and mutation resolves `ExecutionAccess` against that authoritative
  scope and fails closed on missing or mismatched identity.
- Workflow-authored context cannot overwrite journey scope or policy.
- Diagnostics are bounded and sanitized; artifact refs are opaque and
  authorization-scoped.
- Provider credentials never enter policies, snapshots, events, generated
  bundles, or retained artifacts.
- Partial build/evaluation artifacts have finite retention and exact scoped
  cleanup.

## Relationship To The Semantic Compiler

`JourneyExecutionPort` controls execution identity, lifecycle, deadline,
cancellation, resource bounds, lineage, and durable outcomes. It does not own
the application manifest, taxonomy, semantic graph, implementation binding,
`CompilationPlan`, refinement semantics, transition UX, evaluation scoring, or
commercial usage.

Once the semantic compiler exists, journey execution consumes its canonical
graph and build version identifiers. Production implementation of this port
waits until the semantic-compiler ADR determines those identities. Lifecycle
and outbox primitives may be designed independently, but they must not embed
`AppBuildPlan` fields, filesystem artifact families, or legacy semantic
authority.

## OSS And Proprietary Boundary

This is a generic framework mechanism under
[Eval And Build Intelligence Boundary](../architecture/foundations/eval-and-build-intelligence-boundary.md)
and [AG2 Ownership Boundary](../architecture/workflows/ag2-ownership-boundary.md).

Mozaiks OSS owns journey identity, lifecycle/policy schemas, provider-neutral
propagation, deadline/token enforcement checkpoints, cooperative cancellation,
public handles/snapshots/results, adapter integration, local persistence and
observation interfaces, tenant safety, and generic evaluation integration
points/tests.

Mozaiks Cloud may privately own customer evaluation corpora and results, quality
thresholds, strategy selection, repair/refinement rankings, model routing,
learned recommendations, historical build intelligence, hosted dashboards, and
fleet analytics. Those policies may call the public contract but cannot become
a hidden OSS runtime dependency.

## Alternatives Considered

| Alternative | Decision |
|---|---|
| Extend `scripts/run_live_workflow_smoke.py` | Rejected: it completes one workflow and would falsely equate ValueEngine with the build journey. |
| Invoke each workflow from an evaluator | Rejected: duplicates production routing, persistence, advancement, and repair behavior. |
| Use `RunRequest.extra` only | Rejected: arbitrary engine metadata is not typed, immutable, durable, or automatically propagated. |
| Extend `OrchestrationPort` | Rejected: mixes cross-workflow policy and artifacts into the workflow-engine boundary. |
| Expose `JourneyOrchestrator` tasks | Rejected: private process-local tasks cannot provide restart, authorization, or durable completion. |
| Add evaluation-only orchestration | Rejected: creates a second production path and can manufacture apparent success. |
| Use AG2 evaluation budgets | Rejected: they do not own Mozaiks routing/artifacts and are observational rather than a shared atomic reservation authority. |
| Use the token watchdog | Rejected: alerts after observation do not prevent calls or concurrent overspend. |
| Allow unbounded live evaluation | Rejected: opt-in does not constrain loops, concurrency, restart, or spend. |
| Introduce `JourneyExecutionPort` | Chosen: one provider-neutral owner above the existing workflow port. |

## Compatibility And Rollout

Existing `RunRequest`/`ResumeRequest` callers remain unchanged initially when
`journey_scope` is absent. This explicit temporary `legacy_unbounded` mode has
no journey handle or journey-wide guarantee and must be observable so new
public entrypoints do not adopt it. `JourneyExecutionPort.start` always requires
a bounded policy and all capabilities that policy and journey require. A later
pre-1.0 decision may remove legacy mode after all journey entrypoints migrate.

### Phase 1: lifecycle, deadline, observation, and cancellation

Add identity/policy/snapshot/outcome/settlement/store contracts, typed
propagation, start and resume idempotency, pinned sequence validation, atomic
lifecycle/stage claims, durable dispatch intents, public port operations,
deadline enforcement, bounded cooperative cancellation, restart leases,
sanitized artifacts, and execution-outcome persistence. This phase advertises
only the capabilities it proves and does not authorize public live-model starts
or claim token-bounded execution.

### Phase 2: shared token reservation

Add adapter preflight estimation, atomic concurrent reservation, retry/repair
accounting, invocation propagation, and reconciliation of actual, missing,
malformed, duplicate, late, and over-reservation usage.

### Phase 3: evaluation integration

Make the raw-prompt evaluator a caller of `JourneyExecutionPort`, with explicit
live opt-in, model configuration, deadline/token budget, isolated artifact
roots, local sanitized results, and production validators/export/loading. The
live evaluator cannot use `legacy_unbounded` mode and remains disabled until the
lifecycle and shared token-reservation phases are implemented and proven.

### Phase 4: optional operator surfaces

Add authorized CLI/Studio/API projections only when requested. Keep threshold
selection, model routing, hosted dashboards, and fleet analytics outside the
OSS mechanism. Hard cost reservation remains a later decision.

## Acceptance Criteria For Implementation

- A complete multi-workflow journey produces exactly one immutable execution
  outcome; accounting settles separately without changing outcome or reason.
- Child workflows/task batches inherit `journey_id` and have distinct,
  optionally parent-linked `workflow_run_id` values.
- Late provider usage after terminal execution updates accounting only while
  settlement is pending and never advances work or changes the outcome.
- Cancellation immediately prevents later retry, repair, resume, side effect,
  and auto-advancement; its deadline terminates execution despite abandoned
  calls, workers, reservations, or missing usage.
- An expired journey cannot start or resume after process restart.
- Concurrent calls cannot reserve beyond the shared maximum token ceiling.
- Reservation recovery distinguishes crashes before and after durable
  transmission; missing/malformed usage is charged conservatively and duplicate
  invocation events do not double-count.
- A crash after child-advancement commit but before dispatch is recovered from
  its durable intent; duplicate outbox delivery cannot create a second child.
- Every policy counter stops at its exact configured maximum under concurrency
  and reports its counter-specific stable exhaustion reason.
- Terminal-state races follow the documented compare-and-set precedence.
- Every operation remains authorized by immutable creation scope before and
  after any generated app is associated.
- A journey uses its pinned workflow-sequence version despite later registry
  changes, and invalid entrypoint/route combinations fail before persistence.
- Resume after an expired human-review wait returns `timed_out` and does not
  extend the deadline.
- Partial artifacts are scoped, sanitized, retained/removed by policy, and
  cleaned idempotently.
- Existing workflow callers remain compatible in temporary legacy mode.
- A raw-prompt evaluation can await the complete build rather than the first
  workflow, but live evaluation cannot start through legacy mode.

## Consequences

- Production callers and evaluators can observe the same truthful journey.
- Restart-safe cancellation, deadlines, lineage, and token accounting become
  testable framework behavior.
- Each execution boundary must propagate typed scope and perform fail-closed
  checks.
- Conservative token estimation and atomic reservations add implementation and
  concurrency complexity.
- Public policy, lifecycle, accounting, reservation, snapshot, workflow-run,
  outcome, settled-result, and reason schemas become compatibility surfaces.

## Documentation Debt Outside This ADR

The repository currently has two ADR files numbered 0004. Renumbering or
otherwise resolving that documentation debt is intentionally outside this PR;
this proposal remains ADR 0006 and does not modify either existing ADR 0004.

## Affected Invariants

- **#1 Public Framework Contracts Stay Provider-Neutral.** No AG2/provider
  objects enter the public contract.
- **#2 Generated Artifacts Must Not Contain Raw Secrets.** Snapshots and retained
  evidence are sanitized references.
- **#3 Agents Produce Candidates; Deterministic Code Validates and Promotes.**
  Cancelled/expired output cannot progress or promote.
- **#4 Public Schemas and Contracts Are Classified and Versioned.** Policy,
  lifecycle/accounting/reservation state, reason, and observation/result shapes
  are versioned.
- **#5 Generic App Intelligence Can Be OSS; Multi-App Learned Intelligence
  Requires Review.** Mechanism is OSS; corpora and learned strategy stay private.
- **#6 Authority Bypass Semantics Must Not Expand Casually.** Observation and
  cancellation remain authorization-scoped.
- **#8 Operator Capabilities Are Explicitly Separated.** Commercial enforcement
  and hosted policy remain outside journey safety.

## Validation

For this Proposed ADR draft:

- `python -m mkdocs build --strict`
- documentation links checked through strict MkDocs plus local target checks
- `git diff --check`
- exact changed-file review
- verification that PR #394 was not modified

Independent runtime/AG2 architecture, concurrency, tenancy/security, and
compatibility review is required before accepting this ADR or beginning
production implementation.
