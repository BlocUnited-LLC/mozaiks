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
  `mozaiksai/core/tokens/manager.py`, with that middleware also invoking the
  per-call commercial `TokenUsageGuard` preflight;
- `RuntimeUsageLedger` in `mozaiksai/core/usage/ledger.py`;
- the alert-only AG2 token watchdog in
  `mozaiksai/core/usage/watchdog.py`; and
- generated artifact-root resolution through
  `MOZAIKS_GENERATED_ARTIFACTS_PATH` in AgentGenerator and AppGenerator tools.

## Decision

Introduce a distinct provider-neutral `JourneyExecutionPort` above
`OrchestrationPort`.

`OrchestrationPort` remains responsible for one workflow execution. Its
`RunRequest`, `ResumeRequest`, and `RunResult` gain typed journey/run scope, and
its current ambiguous `cancel(run_id: str)` contract is replaced pre-1.0 by a
typed `WorkflowCancelRequest` containing `workflow_run_id`, `chat_id`, and the
same protected journey scope. The string contract has no production caller and
no behavioral test today, so this is a contract replacement, not a caller
migration: slice 3 removes the unused signature and introduces the typed
request directly, and cancellation capability is advertised only after real
authorization, identity, adapter, and cancellation-behavior tests pass. It
does not gain journey start, listing, waiting, artifact retention, or
cross-workflow cancellation methods.

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
| `invocation_id` | One logical provider/model call intent. Server-generated before any transmission. Owns call idempotency, the reservation record, retry grouping, and usage reconciliation. |
| `transmission_id` | One physical provider transmission attempt of a logical invocation. Server-generated and durably claimed before the request may leave the adapter. Owns the `transmitted` fence and the provider-retry attempt claim. |
| provider response ID | Optional provider-observed identity returned after a call. Owns nothing authoritative; retained as provider-response correlation evidence alongside the server-generated identities. |

`journey_id` and `workflow_run_id` are server-generated, opaque, immutable, and
tenant-scoped. They are distinct from `app_id`, subscription/customer usage
identity, `chat_id`, and AG2 identifiers.

Ledger deduplication for journey-scoped usage keys on the server-generated
`invocation_id`/`transmission_id`, never on the provider response ID alone.
Current usage events and the runtime usage ledger use the name `invocation_id`
for the provider-reported response ID; the implementation renames that
observational field when the reservation contract lands so one name never
denotes two identities.

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

The lifecycle foundation slice does not advertise shared token reservation and
therefore must not claim token-bounded execution merely because it persists
`max_total_tokens`. Public live-model journey start remains disabled until
rollout slices 1–5 are implemented and proven and an operator explicitly opts
in. The raw-prompt live evaluator requires the bounded public journey contract
and cannot use `legacy_unbounded` mode.

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

`JourneyScope` and the related identity, policy, and access contract types are
defined in a stdlib-only provider-neutral contracts module under
`mozaiksai/core/ports/` (slice 1's `journey_execution.py`), so
`OrchestrationPort` never imports from session, transport, workflow, or
journey implementation modules and the ports layer keeps its current
stdlib-only dependency direction.

`JourneyStartRequest` contains `ExecutionAccess`, an idempotency key,
`workflow_sequence_id`, one typed entry reference, any route choices already
known at start, initial user message where supported, and the policy. A public
entry uses `workflow_entrypoint_id`; an internal refinement re-entry uses an
entry stage declared by the named sequence. Callers do not submit a second
free-form transition target. Before persisting `pending`, start resolves the
named sequence from the authoritative registry, verifies the entry reference
belongs to it, validates every supplied route choice against the pinned
transition declaration, and pins the fully resolved post-overlay sequence plus
all referenced transition and dependency declarations by content digest. The
canonical serialization and digest calculation for that closed view are defined
by the registry schema owner, `workflow.pack.schema/config`, in rollout slice 0.
Registry `version: 3` is a schema version and is not sufficient as a
journey-definition version.

Later user choices are not required prematurely at start. Each is accepted as
an idempotent journey input addressed by `journey_id`, `workflow_run_id`, stable
pending `input_request_id`, and selected option ID; it is validated against the
pinned transition before advancement. The persisted resolved definition is
authoritative for the journey lifetime, so a later registry or overlay edit
cannot alter an in-progress journey. The canonical idempotency request hash
includes the resolved-definition digest, supplied route choices, entry
reference, immutable creation scope, semantic input references, and complete
policy. Start then uses the existing `SessionRouter` and launcher; no workflow,
task, or model call starts before persistence succeeds.

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
3. Within one atomic checkpoint decision, an expired deadline is evaluated
   before token availability, so simultaneous expiry becomes `timed_out`.
4. Otherwise a requested reservation that would exceed the ceiling becomes
   `budget_exceeded` before provider work starts. Failure to form a conservative
   reservation at all becomes `failed` with `token_reservation_unavailable`.
5. Otherwise the first successful cancellation, failure, or completion
   compare-and-set wins. Completion rechecks every stop condition atomically.

Terminal reason is a separate stable code, such as `journey_completed`,
`caller_requested`, `deadline_expired`, `token_budget_exhausted`,
`token_reservation_unavailable`, `provider_transmission_ambiguous`,
`workflow_failed`, `lifecycle_persistence_unavailable`, or
`orphan_recovery_unsafe`.

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

Allowed transitions are `reserved → transmitted | released_untransmitted`,
`transmitted → reconciled | expired_conservatively`, and
`expired_conservatively → reconciled` only while accounting is `pending`.
`reconciled` and `released_untransmitted` are final states;
`expired_conservatively` is provisional until accounting reaches its final
status and immutable thereafter.

`reserved` records `journey_id`, unique `invocation_id`, conservative amount,
owner/lease identity, lease expiry, and creation version before external
transmission. Duplicate reservation of the same invocation returns the same
record and amount or fails an input-hash conflict; it never reserves twice.
Transmission is made durable by atomically recording a new `transmission_id`
claim and changing the reservation to `transmitted` before the provider
request can leave the adapter boundary.

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
Current OSS deployments run standalone MongoDB without multi-document
transactions, so the default consistency boundary is the fenced
single-document compare-and-set pattern already proven by
`MongoWorkflowQueue`, the reaction idempotency store, and billing fulfillment;
replica-set transactions are an optional strengthening, never an assumed
capability.

`transmitted` is also the ambiguity boundary for retries. Recovery must not
automatically retransmit an invocation merely because no response was recorded.
Retransmission is permitted only when the adapter proves the prior attempt did
not leave the process, or when the provider offers an authoritative idempotency
contract keyed by the same `invocation_id`. Every permitted retransmission
claims a new durable `transmission_id` under the same reservation and consumes
the provider-retry counter. Otherwise the invocation is charged
conservatively, its output remains quarantined, and execution fails with the
stable `provider_transmission_ambiguous` terminal reason. When ambiguous
transmission terminates execution, the outcome is `failed` with that reason;
if another terminal state has already won under the documented precedence, the
conservative charge and quarantine still apply without changing that immutable
outcome. Either way the reservation follows the normal conservative
disposition, so accounting becomes `settlement_failed` unless authoritative
provider usage reconciles the invocation before accounting finalizes. The provider-retry counter never
converts an uncertain external side effect into permission to issue a
duplicate call.

The reservation and transmission fence must sit at a boundary that observes
every physical transmission. Current agent construction in
`mozaiksai/core/workflow/agents/factory.py` appends AG2 `RetryMiddleware`
after the usage middleware, so a retry layer beneath a naive reservation hook
could retransmit without a new durable claim. Bounded execution therefore
requires that no retry layer below the reservation boundary can issue a
transmission lacking its own durable `transmission_id` claim; if AG2
middleware ordering cannot guarantee that, bounded execution replaces or wraps
provider retry with a Mozaiks-owned retry/reservation boundary. Logical
invocations and physical transmissions are never conflated. Accounting
semantics are selected with the execution mode, once per request: transitional
calls keep today's observational accounting, and a bounded request that cannot
mount the reservation boundary fails closed with
`token_reservation_unavailable` instead of silently degrading to
observational accounting.

Accounting cannot remain pending forever. A terminal execution persists an
`accounting_deadline_utc` derived from the policy settlement grace. At that
deadline, every still-`transmitted` reservation becomes
`expired_conservatively` and is charged fully. Accounting status is decided by
final reservation dispositions at settlement: it becomes `settled` only when
every invocation's final state is `reconciled` or `released_untransmitted` — a
reservation that expired conservatively and was later authoritatively
reconciled before settlement finalized counts as `reconciled` — and it becomes
`settlement_failed` when any reservation finishes `expired_conservatively` or
its usage is known missing or malformed. Either final accounting status may be
reached early when all reservations have a final disposition.

The usage fields are:

- `estimated_prompt_tokens`: conservative input estimate before a call;
- `reserved_tokens`: outstanding worst-case capacity;
- `observed_actual_tokens`: provider-reported post-call usage; and
- `reconciled_tokens`: authoritative committed usage.

Post-call accounting alone is rejected as a hard bound because one call can
overshoot before usage is reported.

Current enforcement, stated accurately: the commercial `TokenUsageGuard` runs
as a per-call wallet preflight inside the usage middleware and the
`simple_llm` capability; `simple_llm` estimates a character-ratio input bound
plus configured maximum output, while the AG2 workflow path resolves its
required-token preflight from context keys or environment and defaults to one
token, making that check effectively a nonzero-balance test; actual wallet
debits happen through post-call usage ingestion; and the token watchdog is
advisory. That existing ratio estimate is not conservative and does not
satisfy this contract's estimator requirement. No shared atomic journey
reservation exists today; that is the gap this contract closes, and the
commercial wallet lane remains a separate authority from journey safety.

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

Journey counters compose with existing workflow-local limits instead of
replacing them: `TaskBatchExecution.retry_limit` (AppGenerator's
`app_build_tasks` declares `retry_limit: 1`), AG2 `RetryMiddleware` provider
retries, `AgentReply.content` schema-correction retries,
`bundle_repair_attempt_count`, `workflow_integration_repair_count`, and
AgentGenerator workflow-bundle repair routing. Local limits remain owned by
their existing contracts. An operation is permitted only when its local limit
and every applicable journey counter both allow it; exhaustion of either is
fail-closed. Each counted operation carries a stable operation/attempt ID, so
duplicate delivery returns the existing claim and can neither consume another
claim nor bypass a local limit.

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

The future semantic-compiler ADR must define the identity and version semantics
for the typed references that journey execution consumes:

- `ApplicationManifestRef` for the canonical app/product manifest, including a
  pre-app form that does not invent a final `app_id`;
- `SemanticGraphRef` for semantic graph identity plus immutable version or
  digest;
- `TaxonomyNamespaceRef` for each namespaced taxonomy version used by typed
  references and child contracts;
- `CompilationPlanRef` for the deterministic compilation plan and renderer set;
- `BuildContextBindingRef` for the exact declared build-context inputs and
  projections used by compilation;
- `RefinementPatchRef` for a typed patch/replay operation over a known semantic
  and artifact base;
- `ArtifactRevisionRef` for validated candidate and promoted artifact identity;
  and
- the typed child-contract references covering modules, pages, actions,
  workflows, capabilities, events, reactions, data contracts, and bounded
  Python/JavaScript customization stubs.

These names identify required reference roles only; this ADR does not define
their fields, namespace rules, digest algorithm, graph topology, renderer
contracts, patch format, or validation/promotion semantics. Those decisions
belong to the semantic-compiler ADR and the existing canonical contract owners.
`JourneyScope` may carry the resulting opaque typed references and record which
version ran, but it may not interpret or rewrite them.

`WorkflowSequenceRef` is separate. Its source remains the fully resolved
`extension_registry.json` workflow sequence plus referenced transition and
dependency declarations. The journey store pins that resolved definition by
content digest; the semantic compiler may reference the same sequence but may
not redefine its steps, route choices, transition UX, or advancement rules.

Production `JourneyExecutionPort.start`, production capability advertisement,
and any public live-model journey entrypoint are blocked until the
semantic-compiler ADR is accepted and those references exist. Generic lifecycle
models, store prototypes, CAS tests, and outbox tests may be developed earlier
only behind non-production test seams; they may not start workflows, mutate
generated artifacts, advertise a production capability, or establish temporary
semantic identities. No rollout slice below may cross that gate implicitly.

## Current Authority And Migration Boundary

Each current subsystem has exactly one disposition. “Separate authority” means
journey execution may carry a reference, enforce a stop checkpoint, or observe
an outcome, but does not become that subsystem's source of truth.

| Current component and source of truth | Current responsibility | Disposition after this ADR | Overlap and migration action | Compatibility, tests, and removal condition |
|---|---|---|---|---|
| `factory_app/workflows/extended_orchestration/extension_registry.json`; `workflow.pack.schema/config` | Sequences, entrypoints, workflow dependencies, and transition declarations | retained unchanged | Journey start resolves and pins it; it never writes an alternate sequence graph. | Registry closure/digest/overlay tests. No removal. |
| `SessionRouter`, `launch_transition`, `/api/transitions/resolve`, transition UI | Route resolution, user-choice validation, launch navigation | retained unchanged | Bounded inputs call the same resolver against the pinned definition; the journey store records claims/outcomes only. | Existing router/UI tests plus pinned-choice and duplicate-input tests. No removal. |
| `SessionState` and `SessionStateStore` | One app/user navigation snapshot, refinement state, and ambiguous journey fields | migrated into `JourneyExecutionPort` | Move only `journey_instance_id`, `journey_key`, `journey_position`, `journey_total_steps`, and bounded sequence lifecycle to journey records. These four fields are also denormalized onto chat-session documents by `SessionRouter` and read from those documents by `SessionRouter` group-completion queries and the factory build-lifecycle hooks in `factory_app/workflows/_shared/platform/build_lifecycle.py`; bounded journeys migrate those readers to journey records too. Keep chat navigation, pending harness decisions, revision history, and artifact refs here. | Migration tests prove bounded reads — including chat-document copies and build-lifecycle hooks — use only the journey store. Remove migrated fields when compatibility mode retires. |
| `AG2PersistenceManager` chat documents and run streams | `chat_id`, messages, pending input, UI state, AG2 run trace | separate authority that ADR 0006 must not absorb | Add immutable `workflow_run_id`/`journey_id` correlation and fenced pending-input claims; do not copy transcript ownership into journey snapshots. | Chat replay/persistence and scoped pending-input tests. No removal. |
| `WorkflowBridgeMixin`, `SimpleTransport`, and transport handlers | Workflow input, resume, run completion, and process-live run handles — `SimpleTransport` owns the `_live_ag2_workflow_runs` registry and accessors; `WorkflowBridgeMixin` consumes and projects those handles without owning the registry | extended by ADR 0006 | Route bounded calls through typed run/resume/cancel requests and durable claims; retain transport projection. | Duplicate resume, expired review, late output, replay, and transitional-call tests. |
| `JourneyOrchestrator` plus `SimpleTransport._background_tasks` | Best-effort process-local auto-advancement | removed after migration | Replace bounded advancement with durable child claims/outbox delivery; never run both advancement paths for one bounded journey. | Crash/replay/duplicate delivery tests. Remove after every journey entrypoint uses durable dispatch and rollback window closes. |
| `OrchestrationPort`, `AG2OrchestrationAdapter`, `AG2NetworkRunner` | One workflow run through AG2 and Mozaiks result projection | extended by ADR 0006 | Add typed scope/results/cancel request and checkpoints; journey execution calls this one engine path. | Port, adapter, AG2 alignment, cancellation, and scope-propagation tests. No engine replacement. |
| `WorkflowQueue` / `MongoWorkflowQueue` | Generic at-least-once delivery, leases, fencing, bounded retries; currently dormant — no production caller enqueues, and the default backend is `noop` | extended by ADR 0006 | Reuse it as the delivery projection for durable journey intents. The journey store owns the intent/child claim; do not add a second generic queue. `NoOpWorkflowQueue` cannot advertise durable dispatch. | Queue lease/fencing plus journey outbox projection tests. No removal. |
| `task_batches.py` and `AG2TaskBatchRunner` | Deterministic dependency scheduling, owned-path merge, AG2 `Task` evidence | extended by ADR 0006 | Add typed scope, stable attempt IDs, shared claims/reservations, deadline, and quarantine checks; keep AG2 task lifecycle observational. | Concurrent attempt limit, cancellation, timeout, identity, and merge-quarantine tests. |
| AG2 1.0.2 `Hub`, `WorkflowAdapter`, `TransitionGraph`, `Task`, streams, and events | Agent/network execution, routing fold, task lifecycle, WAL/event mechanics | retained unchanged | Use native cancellation/task/event hooks where available; Mozaiks owns only durable product policy around them. | Real-package alignment tests. Revisit local adapters only when AG2 supplies an equivalent primitive. |
| Usage middleware, structured-output validation/correction, workflow and generated-bundle repair routes | Model usage observation — the same middleware also hosts the separate per-call commercial `TokenUsageGuard` preflight — and bounded workflow-local correction/repair behavior | extended by ADR 0006 | Add exact attempt/invocation claims and stop checkpoints; do not move repair-selection semantics into journey execution. | Retry/correction/repair counter, late usage, and no-progress tests. Existing repair state remains until its owning contract migrates. |
| `AppBuildPlan`, app/design schemas, module/event/reaction contracts, materializers, validators, AppLoader, export/deployment | Semantic application structure and deterministic artifact production | unresolved pending the semantic-compiler ADR | Journey execution consumes typed refs only and cannot embed these shapes or infer artifact families. | Compiler contract, reference-closure, materialization, loader, and generated-app acceptance tests defined by that ADR. No removal authorized here. |
| Named `build_context` registries, projected context variables, prompt/system-message assembly, AG2 `KnowledgeStore` seam | Build inputs, workflow state, prompts, and agent memory | separate authority that ADR 0006 must not absorb | Pin `BuildContextBindingRef`; journey policy cannot rewrite catalogs, prompts, context authority, or knowledge contents. | Binding digest, context authority, prompt assembly, and tenant-isolation tests. No removal. |
| `mozaiksai/control_plane` and `factory_app/refinement_harness` | Change classification, checkpoint routing, patch/staging policy, attempt meaning | separate authority that ADR 0006 must not absorb | It requests a declared sequence and supplies `RefinementPatchRef`; journey execution counts starts and enforces stops only. | Existing refinement routing/staging/promotion tests plus bounded re-entry claims. No removal. |
| `ArtifactStore` (the `mozaiksai/core/artifacts` build-record store, distinct from the `core/ports/artifact_store.py` blob-store port), content store, generated roots, AppReview and control-plane promotion | Artifact lineage, validation, review, retention, and promotion authority | separate authority that ADR 0006 must not absorb | Add scoped refs/quarantine checks. A completed journey is necessary but never sufficient for promotion. | Cross-journey cleanup, late-write quarantine, validation, review, and promotion authorization tests. No removal. |
| `factory_app/eval/bundle_eval.py` and `bundle_scorers.py` | Deterministic artifact scoring, local result persistence and comparison | separate authority that ADR 0006 must not absorb | Evaluators call `wait_settled`; journey execution supplies evidence but never selects scorers, thresholds, or verdicts. | Existing scorer/diff tests plus settled-result and isolated-root integration tests. No removal. |
| `RuntimeUsageLedger`, `TokenManager`, and token watchdog | Factual post-call usage/cost observation and advisory alerts | extended by ADR 0006 | Add scoped IDs and reconcile evidence into the journey reservation owner; these stores do not become reservation authority. | Duplicate/missing/late usage and ledger compatibility tests. No removal. |
| `TokenUsageGuard`, token wallet, subscriptions/entitlements, `usage_limits`, and MozaiksPay facade | Commercial eligibility, balances, quotas, and provider-neutral billing contracts | separate authority that ADR 0006 must not absorb | Commercial checks remain independent and may deny before journey reservation; journey settlement never debits or grants them. | Existing entitlement/wallet/idempotency tests plus proof either authority can deny independently. No removal. |
| `UserPrincipal`, tenant/workspace claims, app/chat scope validation | Authentication and current request authorization | separate authority that ADR 0006 must not absorb | `ExecutionAccess` adapts the authenticated principal into an immutable creation-scope ref; it does not create tenant/workspace identity. | Cross-tenant, pre-app, reassociation, app/chat mismatch, and unauthorized observation/cancel tests. No removal. |
| Build-events outbox and reaction idempotency stores | Domain-specific event delivery/idempotency | separate authority that ADR 0006 must not absorb | Do not reuse them as journey lifecycle or dispatch stores; correlate by opaque refs only. | Existing outbox/reaction tests plus proof journey replay does not redeliver domain events. No removal. |

### Deferred Deletion Candidates

This ADR authorizes no deletion. The only current behavior identified for later
removal is behavior whose authority moves into the durable journey path:

| Candidate | Replacement | Migration prerequisite and proof | Rollback consideration |
|---|---|---|---|
| `SessionState.journey_instance_id`, `journey_key`, `journey_position`, and `journey_total_steps`, including their denormalized chat-document copies | Durable journey identity, pinned sequence ref, and stage records | All entrypoints bounded; active transitional sessions drained; migration tests prove bounded reads — including `SessionRouter` chat-document queries and `factory_app/workflows/_shared/platform/build_lifecycle.py` hooks — never consult old fields. | Roll back before field deletion; do not restore dual-write after deletion. |
| `JourneyOrchestrator._inflight` and bounded use of `SimpleTransport._background_tasks` | Durable child claim, journey outbox, queue delivery, and fencing | Restart, duplicate delivery, cancelled-intent, and multi-worker tests pass; operational drain is proven. | Disable new starts and drain with the durable version; never fall back in flight. |
| `OrchestrationPort.cancel(run_id: str)` where `run_id` means `chat_id` | Typed `WorkflowCancelRequest` | The current signature has no production caller or behavioral test; authorization, identity, adapter, and cancellation-behavior tests pass before `cooperative_cancellation` is advertised. | Remove the unused string signature directly in slice 3 when the typed request lands; no transitional retention is needed because no caller exists. |
| `legacy_unbounded` public compatibility mode | `bounded_only_v1` | Every supported start/resume/refinement/evaluation caller advertises and requires the bounded capabilities. | Deployment rollback is permitted before removal; per-request fallback or dual execution is not. |

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
a bounded policy and all capabilities that policy and journey require.

Compatibility is selected once per request, before work starts. A bounded
journey uses only durable journey advancement; a transitional call uses only the
existing process-local path. There is no dual-write, shadow advancement, or
failover from a failed bounded journey into transitional execution. Rollback may
disable new bounded starts and drain/cancel existing bounded records with the
same implementation version; it may not re-run them through transitional mode.

The slices below are sequential promotion gates. “Tests” means those tests pass
before the slice advertises its capability or the next slice begins. Every new
public record and capability is versioned.

| Slice and exact production components | Contracts introduced; old behavior replaced; compatibility | Required tests and advertised capability | Migration, rollback, deletion, live-model permission, compiler dependency |
|---|---|---|---|
| **0. Semantic-compiler prerequisite** — future compiler owner plus `workflow.pack.schema/config` | Accept the semantic-compiler ADR; implement the typed refs named above; define a canonical digest for the fully resolved sequence/transition/dependency view. No journey runtime yet. | Compiler reference-closure, digest stability, overlay, manifest/graph/plan/materializer/promotion tests. Advertises no journey capability. | No migration or live calls. Mandatory before production slice 1; test-only lifecycle prototypes remain non-production. |
| **1. Lifecycle/store/identity foundations** — new `mozaiksai/core/ports/journey_execution.py` and narrow `mozaiksai/core/journey_execution/` owner; `session/model.py`, `session/persistence.py`, `session/router.py`; registry resolver | Add versioned policy/scope/access/snapshot/outcome/settlement/reason contracts, immutable creation scope, idempotent start/input keys, pinned refs, CAS lifecycle, leases, counters, and accounting deadline. Bounded records are authoritative; transitional session fields are read only by transitional mode. | New lifecycle model/store tests cover duplicate starts, input conflicts, CAS races, terminal precedence, unknown reasons, authorization, reassociation, leases, and partial capabilities. Advertises `durable_lifecycle`, `pinned_definition`, and `durable_observation` only. | Backfill is unnecessary for old chats; they remain transitional. Rollback disables new bounded starts and drains records. Delete nothing yet. No live models. Requires slice 0. |
| **2. Durable child dispatch and recovery** — `workflow/pack/journey_orchestrator.py`, `workflow/queue.py`, `transport/workflow_bridge.py`, `SimpleTransport._background_tasks` | Add durable parent/stage/child claims and journey outbox records; project deterministic items into `WorkflowQueue`; suppress stopped intents. For bounded journeys, replace process-local advancement rather than wrapping it. | Crash-before-dispatch, duplicate delivery, expired lease, stale fencing token, parent replay, stopped-intent, queue-disabled, and process-restart tests plus existing queue/journey tests. Advertises `durable_dispatch` and `restart_recovery`. | Feature flag may stop dispatch while preserving records; same-version workers drain or cancel. Delete bounded use of `_background_tasks` only after recovery proof. No live models. Requires slice 1 and compiler refs. |
| **3. Typed run/resume/task-batch propagation** — `ports/orchestration.py`, `adapters/ag2_orchestration.py`, `adapters/ag2_network_runner.py`, `transport/workflow_bridge.py`, `workflow/task_batches.py`, `adapters/ag2_task_batch_runner.py` | Add `JourneyScope` to run/resume/task requests and results; replace string cancel with `WorkflowCancelRequest`; add stable workflow, checkpoint, task-attempt, and invocation IDs. Transitional requests omit scope; bounded requests fail if any boundary drops it. | Existing port/AG2/task-batch tests plus end-to-end scope, duplicate resume, task identity, parent lineage, serialization, and missing-scope fail-closed tests. Advertises `typed_scope_propagation`. | Roll back by blocking new bounded starts, never by stripping scope in flight. Delete chat-as-run-ID semantics after compatibility retirement. No live models. Requires slices 1–2 and compiler refs. |
| **4. Deadline and cooperative cancellation** — journey owner, `ag2_orchestration.py`, `ag2_network_runner.py`, `orchestration_patterns.py`, `workflow_bridge.py`, task batches, structured-output correction, repair/refinement entry adapters, artifact/export checkpoints | Add persisted UTC/derived monotonic deadlines, cancellation grace, provider/task signals, quarantine fences, and atomic completion recheck. Replace process-task cancellation as authority; retain it only as a signal. | Deadline/cancel/completion race matrix; provider ignores cancel; late output; human review expiry; restart; repair/correction retry; artifact/export quarantine; cleanup retry tests. Advertises `deadline_enforcement` and `cooperative_cancellation`. | Rollback blocks starts and lets same-version workers reach terminal state. Remove bounded reliance on transport task maps after proof. No live models. Requires slices 1–3 and compiler refs. |
| **5. Shared token reservation and settlement** — journey reservation store, `usage/middleware.py`, `tokens/manager.py`, `usage/ledger.py`, `workflow/agents/factory.py`, AG2 run/task adapters | Add invocation reservation state machine, conservative estimator/max-output contract, atomic journey counters, transmission fence, idempotent reconciliation, ambiguous-transmission rule, and finite settlement. Existing ledgers remain observational/commercially separate. | Concurrent reservations/task batches; crash before/after transmission; provider idempotency; missing/malformed/duplicate/late/over usage; settlement timeout; deadline/cancel/budget races; wallet/quota separation tests. Advertises `shared_token_reservation` and `accounting_settlement`. | Rollback blocks new bounded starts; outstanding reservations settle with the same version. No reservation record is converted to wallet state. Public live starts become eligible only after all slices 1–5 pass and an operator explicitly opts in; never through `NoOpWorkflowQueue` or a non-durable store. Requires compiler refs. |
| **6. Evaluator integration** — `factory_app/eval/bundle_eval.py`, `bundle_scorers.py`, a new bounded raw-prompt runner, validators/export/loading; `scripts/run_live_workflow_smoke.py` remains a one-workflow diagnostic | Add versioned evaluation-run/result refs over `wait_terminal` and `wait_settled`, isolated journey/artifact roots, explicit model/policy input, sanitized local evidence, and comparison keys. Do not copy scorer policy into the journey port. | Fake-port evaluator tests; terminal-vs-settled; settlement failure; isolation/cleanup; production validator/export/load; result persistence/comparison; deterministic scorer regression. Advertises `bounded_evaluation_input`. | Disable evaluator without affecting journey execution. No transitional failover. Explicitly opted-in live evaluation is permitted only with slices 1–5 capabilities; default remains offline/fake. Requires compiler refs and canonical artifact revisions. |
| **7. Compatibility-mode retirement** — `session/model.py`, `session/persistence.py`, `session/router.py`, `journey_orchestrator.py`, `workflow_bridge.py`, public workflow entrypoints | Migrate every supported entrypoint to bounded start; remove `legacy_unbounded`, old journey fields, ambiguous `journey_id`/`journey_key`/`journey_instance_id`, string cancel, and process-local auto-advancement. There is one orchestration path after this slice. | Repository search/hygiene guard, all workflow launch/resume/transition/refinement tests, migration fixtures, generated-app acceptance, restart and rollback rehearsal. Advertises `bounded_only_v1`. | Cutover only when no supported caller needs transitional fields and all active transitional chats are completed or explicitly abandoned. Rollback is deployment rollback before deletion; no dual-read shim is added. Live models follow slice 5 policy. Requires slices 1–6 and compiler refs. |
| **8. Optional operator surfaces** — authorized runtime/Studio routers, CLI, and Studio UI only if separately requested | Project start/snapshot/list/wait/cancel and diagnostics through existing auth/scope adapters. Do not add operator policy, scorer selection, model routing, or hosted dashboards to the port. | API/CLI/UI authorization, pagination, schema-version, sanitization, unknown-reason, and tenant-isolation tests. Advertises only the surfaces actually shipped. | Surfaces can be removed without changing durable execution. They do not themselves authorize live calls. Requires the capabilities each surface exposes. |

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
- An ambiguously transmitted invocation is never retried unless non-transmission
  is proved or the provider enforces idempotency for the same `invocation_id`.
- Every physical provider transmission is preceded by a durable reservation and
  `transmission_id` claim, including transmissions issued by any retry layer.
- A crash after child-advancement commit but before dispatch is recovered from
  its durable intent; duplicate outbox delivery cannot create a second child.
- Every policy counter stops at its exact configured maximum under concurrency
  and reports its counter-specific stable exhaustion reason.
- Terminal-state races follow the documented compare-and-set precedence.
- Every operation remains authorized by immutable creation scope before and
  after any generated app is associated.
- A journey uses its pinned workflow-sequence version despite later registry
  changes, and invalid entrypoint/route combinations fail before persistence.
- Later route choices are validated and idempotently claimed against the pinned
  transition definition and stable pending input request.
- Resume after an expired human-review wait returns `timed_out` and does not
  extend the deadline.
- Partial artifacts are scoped, sanitized, retained/removed by policy, and
  cleaned idempotently.
- Existing workflow callers remain compatible in temporary compatibility mode.
- A raw-prompt evaluation can await the complete build rather than the first
  workflow, but live evaluation cannot start through compatibility mode.
- Session/chat identity, sequence/transition routing, semantic application
  structure, build contexts, refinement, scoring, promotion, commercial
  accounting, tenant authorization, and AG2 execution retain the single owners
  named in the authority matrix.

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
