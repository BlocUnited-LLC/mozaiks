# ADR 0011: Factory Bounded Task Recovery

Date: 2026-09-19

Status: Accepted

## Context

The preserved greenfield build at OSS candidate `480c17ec` lost the
task-management module prerequisite and drained its model, service, and page
tasks. Assembly replaced execution results with a summary, so the original
module rejection cannot be recovered. Successful auth tasks also ran without
their actual prerequisite contracts; the handler implemented `UserSessionHandler`
instead of the declared `UserAuthenticationModule` and omitted a declared action.
Artifact repair selected ServiceAgent while app, module, and page completeness
remained unresolved. A separate preserved failure showed ServiceAgent writing
`backend/schemas.py` outside its approved ownership.

Dependency draining is correct: an unsuccessful prerequisite cannot authorize
dependent generation. An artifact-only repair cannot recover unexecuted tasks.
The existing Factory policy, task-batch executor, and AG2 worker adapter already
own these responsibilities. Adding a recovery scheduler or persistence store
would create a competing authority.

## Decision

The approved AppBuildPlan remains the task and ownership authority. Normalize
module dependencies after all missing module lanes are synthesized. Models
receive the module contract and applicable persistence contract; services also
receive accepted schemas. Preserve explicit edges, validate the resulting DAG,
and repeat validation after persistence-task consolidation. Normalization does
not invent revision or brownfield work.

The existing batch result is execution evidence. Successful outputs remain at
their task IDs; `_failed` retains typed errors, rejected candidates, attempt
counts, and blocked prerequisites. `_meta` records input/accepted-output digests,
pending and in-flight tasks, consumed recovery roots, and bounded failure history.
Assembly writes a separate summary and never overwrites this evidence.

An optional `TaskBatchRecovery` contract on the same batch declares the Factory
trigger, request key, outcome key, status key, and immutable input keys. Factory
requests identifiers and an input fingerprint, never replacement task definitions
or reconstructed diagnostics. Runtime revalidates the original inventory, DAG,
owners, accepted-output digests, input contracts, and remaining attempt budgets.

For the opted-in AppGenerator batch, the first output-contract rejection is
retained and deferred to Factory policy. An eligible failed root receives one
corrective AG2 call with its exact rejection and candidate, within the original
`retry_limit + 1` ceiling of four calls. Its dependency-blocked descendants resume
through the same executor and retain their own original finite retry budgets.
Successful tasks are seeded from verified outputs and do not execute again.
Changed inputs, missing historical diagnostics, exhausted or consumed attempts,
and uncertain interrupted execution remain blocked. Infrastructure failures are
not reclassified as repairable output errors.

Before dispatch and after settlement, declared task state is committed through
the existing authorized context bridge and AG2 `EV_CONTEXT_SET`. The parent
reply is not committed until its output hook succeeds. A cancellation retains
the in-flight reservation and original rejection history; it cannot silently
authorize replay. Recovery fingerprints include the trusted parent channel ID
from the AG2 packet. A missing or different channel cannot reuse evidence from
another run. Before each agent turn and its auto tools, the bridge restores the
current Hub context, replacing stale local state rather than merging it. Missing
Hub keys cannot resurrect earlier task evidence or repair budgets.

The canonical orchestration adapter injects the existing tenant/chat-scoped
Mongo AG2 KnowledgeStore and requires the original channel on resume. Direct
runner callers may use the existing in-memory store; they must retain that
store/channel to resume. This extends current hydration and packet checkpoint
seams, without a new journal, scheduler, worker implementation, or database
collection.

Artifact repair is scoped to one accepted task from the approved inventory.
Exact diagnostic paths determine ownership; filename-to-agent heuristics are
removed. Failed/unstarted tasks use batch recovery, not artifact repair.
Workflow-integration diagnostics join the same artifact policy and existing
two-proposal budget. Per-task failure fingerprints prevent one unchanged,
rejected, or interrupted target from starving an independent eligible target.
After a blocked batch correction, validation runs once more to select remaining
independent artifact work. The consumed request cannot dispatch again; the batch
clears its routing projection to idle when there is no new recovery request.
An interrupted top-level artifact worker is also uncertain: AG2 may have retained
a pending turn before its result was committed. The canonical `AgentSpec` adds
`pending_turn_replay: allow | block`, defaulting to existing `allow` behavior.
AppGenerator's eight task/repair workers declare `block`. Before resuming any
pending turns, the existing adapter checks AG2's pending records and stops with
the agent and channel when a blocked worker is present. Fresh deliveries are
unchanged. This uses AG2's existing uncertainty evidence rather than inventing
another attempt journal or claiming the worker completed.
No repair may broaden ownership, delete a required final artifact, or discard
unrelated accepted output. Save tools validate an entire candidate before
mutation; AppSchema repair stages the existing deterministic materializer first.

Task path admission also accepts the existing canonical `security/secrets.yaml`
artifact, using `APP_SECURITY_SECRETS_PATH` rather than a second path definition.
The exact normalized path is allowed only after absolute-path, traversal, and
glob checks. Other secret-like paths remain rejected. This resolves the previous
contradiction between approved plan ownership and runtime admission; it does not
permit credential values. Existing names-first generation and runtime content
validators still reject raw secrets in the manifest.

Acceptance checks the complete final snapshot against the approved plan and
execution evidence, in addition to existing schema, module, wiring, runtime,
functional, and quality gates. Passing evidence includes a digest of the plan,
task inventory/results, build binding, and exact file contents. Export verifies
the actual canonical archive against that evidence before contacting GitHub.
Validation and download share one projection of `generated_files` plus the
admitted `code_files` overlay and `deleted_files` tombstones. Raw historical
responses never participate in that projection, including when the current
snapshot or task evidence is empty. Download does not reload files from a prior
generated directory. This prevents rejected changes/deletions and older foreign
readbacks from bypassing save admission. Authorized deterministic renderers add
their artifacts before final acceptance; migration-history registration does not
rewrite an accepted file. Acceptance, the download archive, and export use the
same resulting snapshot.

## Workflow and contract changes

| Surface | Responsibility |
| --- | --- |
| `AppGenerator/tools/app_plan_review.py` | Synthesize prerequisites, reject ambiguity/cycles, retain explicit edges |
| `extended_orchestration/task_batches.yaml` | Opt into bounded recovery on the existing batch |
| Runtime `task_batches.py` | Typed recovery request/failure/config, original DAG execution and finite budgets |
| Runtime `path_ownership.py` | Admit the exact canonical names-first secret policy path while retaining path safety checks |
| Runtime `orchestration_patterns.py`, `ag2_network_runner.py`, `agents/factory.py` | Declared trigger, trusted Hub context hydration, channel lineage, and authorized AG2 checkpoints |
| `context_variables.yaml` | Persist authoritative batch evidence, repair state, requests, and routing projections with closed writers |
| `agents.yaml`, canonical `AgentSpec` | Consume actual prerequisite contracts and exact repair scope; declare pending worker replay policy |
| `transition_graph.yaml`, `tools.yaml` | Route recovery progress to assembly; route independent owned repairs through validation |
| `tools/task_integrity.py`, save tools | Ownership admission, atomic rejection, planned completeness, snapshot digests |
| `tools/repair_policy.py`, `app_validation.py` | Evidence-based recovery selection and one artifact-repair policy |
| Assembly and export tools | Preserve evidence/accepted outputs and require final snapshot acceptance |

The structured worker-output models and runtime module/page contracts remain
canonical. No new LLM-authored recovery schema is introduced: recovery requests
are strict runtime models populated by deterministic Factory policy. Existing
materializers retain identity/reference/scaffold serialization. The handler-name
fix is to supply ServiceAgent the accepted module contract and require the exact
class plus all action methods. An AST rename cannot supply absent behavior.

The E2B missing `package.json` failure is a validation-environment issue. It does
not create a worker repair target or justify an app-owned npm project. Environment
validation starts after deterministic acceptance; installed-package/live proof
remains a separate operator-coordinated check.

## Architecture fit and rejected alternatives

These are public runtime and workflow contracts over one app's approved plan and
execution evidence. The deterministic repair policy uses no learned rankings,
private customer corpus, or cross-app outcome data. MIT publication supports the
same generation guarantees for self-hosted apps; the OSS/commercial boundary is
unchanged. Recovery is additive to `TaskBatchesConfig` version 1 and writes
`evidence_version: 1`; `pending_turn_replay` is an additive optional `AgentSpec`
field. The coordinated package revision owns these contracts and their migration.

This preserves ADR 0007's semantic authority and ADR 0010's separation of agent
execution from generated-app validation. The direction recorded in issue #411
was evaluated: this repair connects existing approved contracts and their
projections; it does not add a second semantic model or decision ledger.

Rejected: raising retry limits, relaxing dependencies/ownership, replacing
accepted outputs with summaries, making ServiceAgent a universal repair owner,
inferring missing historical errors, replaying uncertain attempts, class renaming
as evidence of complete behavior, and a separate recovery queue or store.

AG2 remains responsible for worker execution, Hub/WAL state, and graph progression.
Revisit the scoped packet checkpoint adapter if AG2 exposes an atomic public
output-hook checkpoint/continuation API; see watchpoints AG2-WP-008 and AG2-WP-009.

## Migration and rollback

Deploy runtime code and Factory YAML/prompts together. New opted-in runs write
versioned execution evidence. Recovery requires both complete trustworthy
evidence and the original Hub channel. Runs missing either remain inspectable
but are not automatically reconstructed or resumed.
Status-only acceptance records cannot authorize export; rerun deterministic
acceptance of an available complete snapshot. No database migration, new service,
or background data rewrite is required.

Stop affected in-flight generation before changing versions. Roll back the
coordinated commit set, including YAML, tools, runtime, and prompts. Do not resume
a new-contract in-flight run on the older runtime or selectively revert its
ownership/completeness gate. Preserve its evidence for inspection and start a
fresh build after resolving the failure. Already exported app bundles keep their
existing runtime contracts. Generated workflow bundles declaring
`pending_turn_replay` require the matching runtime; restore their prior compatible
workflow bundle when rolling back rather than silently dropping the guard.

## Independently testable slices and acceptance

1. Dependency normalization: absent module tasks, existing edges, multiple
   modules, repeated normalization, ambiguous owners, and post-merge cycles.
2. Execution evidence/checkpoints: exact rejection retention, accepted-output
   preservation, root correction, descendants, finite budgets, stale inputs,
   interruption, trusted channel lineage, stale bridge replacement, and real
   AG2 Hub reopen without an advancing failed packet or replaying an uncertain
   top-level repair worker.
3. Artifact admission and completeness: ServiceAgent cannot change/delete
   ModelAgent schemas; schema staging is atomic; omitted planned files and
   changed task evidence block acceptance/export.
4. Routing: independent Model/Service/page/config targets, exhausted or unchanged
   lanes, partial recovery assembly, and mandatory quality checks.
5. Offline integration: prerequisite success, invalid service output, retained
   rejection, bounded correction, released page task, deterministic auth scaffold,
   and complete-app acceptance through the actual Factory tools.

The offline proof uses deterministic AG2 worker responses and the real Factory
executor/materializers/acceptance. It does not claim stochastic generation,
installed-package behavior, E2B readiness, or live greenfield success. Those are
the subsequent Codex 1 M-App acceptance handoff.
