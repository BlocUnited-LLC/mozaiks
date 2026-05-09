---
title: SessionRouter
status: Authoritative - Pre-Production, No Backward Compat
created: 2026-04-13
depends_on:
  - REFINEMENT_CONTROL_PLANE_SPEC.md
  - workflow-routing-gates.md
  - docs/reference/deep-dives/mid-flight-journeys.md
  - docs/reference/deep-dives/universal-orchestrator.md
---

# SessionRouter

This document defines the SessionRouter — the unified session-level coordinator that
sits above `JourneyOrchestrator`, host-supplied trigger routing policy, and MFJ.

The concrete refinement re-entry policy is framework-owned. The generic
control-plane contracts and runtime now live under `mozaiksai/control_plane/`,
while the first-party factory pack stays declarative under
`factory_app/control_plane/`. The Studio/Mozaiks host injects the harness into
SessionRouter through a trigger-route resolver seam. SessionRouter does not
import factory pack policy directly.

---

## Why This Layer Exists

The platform already has the right subsystems:

| Subsystem | Scope |
|---|---|
| `GlobalPackGraph` | Static macro topology — workflows, dependencies, journeys, transitions |
| `JourneyOrchestrator` | Reactive auto-advance — listens to `run_complete`, spawns next step |
| MFJ | Workflow-local parallel execution — internal to a single workflow |
| Refinement re-entry policy | Change-class + artifact ownership → re-entry workflow |

What is missing is a **first-class, persisted session record** and a **routing decision point** that sees all trigger types uniformly.

Currently:
- Session position is implicit — scattered `journey_id`, `journey_key`, `journey_step_index`
  fields embedded across individual chat session documents.
- `JourneyOrchestrator` reads those fields reactively but is hardwired to transport internals.
- Refinement triggers via `/api/workflows/trigger` bypass `JourneyOrchestrator` entirely.
- Approval callbacks, transition resolutions, and resume events have no single coordination point.

The result: lifecycle state lives nowhere; each trigger type re-derives position from scratch.

SessionRouter closes that gap. It does **not** replace any existing subsystem — it
coordinates them.

---

## Non-Goals

- Does not replace MFJ. MFJ is workflow-local orchestration and stays that way.
- Does not replace `JourneyOrchestrator`. Journey auto-advance remains event-driven.
- Does not classify natural-language change requests. SessionRouter consumes a prior
  classification result for refinement re-entry.
- Does not stream workflow state across processes. Streaming is a transport/observability
  concern. Add it later if cross-process live coordination becomes necessary.
- Does not own agent routing. Agent handoffs live in `handoffs.yaml`.

---

## Relationship to Existing Layers

```
                    [ User / API ]
                          │
                    SessionRouter             ← unified decision point
                 /      |       |       \
      dependency check  |   journey     refinement
                        |   advance      re-entry
                  transition surface        |
                        |                   |
               chat-ui transition system  re-entry policy helper
                        |                   |
                    GlobalPackGraph         |
                        |                   |
                   MFJ / AG2 runtime        |
```

`JourneyOrchestrator` becomes a sub-handler for the `run_complete` case.
Refinement re-entry policy becomes a host-supplied helper for the `refinement` case.
`GlobalPackGraph` is consulted by SessionRouter (currently also by JourneyOrchestrator —
that duplication goes away once SessionRouter owns the journey position read).

---

## Control-Flow Primitives

SessionRouter should reason about four distinct primitives. These should not be
collapsed into one overloaded routing model.

### 1. Dependencies

Dependencies are the hard prerequisite system.

- They answer: "what must already be complete before this workflow may run?"
- They are declared on `workflows[]`.
- They are enforced by SessionRouter before a workflow starts.
- They are not UI.

Example:

- `AgentGenerator` depends on `DesignDocs`
- `DesignDocs` depends on `ValueEngine`

If a user tries to enter `AgentGenerator` directly, SessionRouter should detect
the first unmet dependency and reroute to it.

### 2. Journeys

Journeys are the guided sequence layer.

- They answer: "what is the intended happy-path order across workflows?"
- They are declared on `workflow_sequences[]`.
- They may include serial or parallel step groups.
- They are optional UX guidance, not the universal prerequisite source.

Dependencies remain the hard truth. Journeys are the guided path.

### 3. Transition Surfaces

Transitions are optional session-router-driven UX surfaces that appear between
workflow phases or before routing.

- They answer: "should this routing decision be surfaced to the user?"
- They may render overlay, inline, progress, confirm, or no UI at all.
- They are presentation wrappers around router decisions, not the router itself.

Some transitions are fully silent. In those cases SessionRouter simply reroutes
or resumes without mounting any UI.

### 4. MFJ

MFJ remains workflow-local orchestration.

- It answers: "inside this workflow, do we need fan-out/fan-in?"
- SessionRouter does not inspect child chat details.
- SessionRouter only sees the outer workflow run lifecycle.

---

## Responsibilities

SessionRouter owns exactly seven things:

1. **Trigger classification** — identify what kind of event arrived
   (initial start, transition resolution, run completion, refinement, approval, resume)

2. **Session load/create** — load the `Session` record for `(app_id, user_id)` scope,
   or create a new one

3. **Dependency enforcement** — before any workflow start or resume, verify hard
   prerequisites from `workflows[].dependencies`

4. **Position resolution** — given the session's current `journey_position` and the
   `GlobalPackGraph`, determine what is valid next

5. **Delegation** — call the correct subsystem for the routing decision

6. **State write-back** — persist updated `Session` state after each decision

7. **Lifecycle event emission** — emit structured events for observability
   (`session.started`, `session.phase_advanced`, `session.refinement_entered`,
   `session.awaiting_transition`, `session.completed`, `session.stale`)

SessionRouter does **not** execute workflows. It decides, persists, and delegates.

---

## Trigger Types

| Trigger source | Description | Delegates to |
|---|---|---|
| `initial` | New session, no prior state | GlobalPackGraph → dependency check → transition or first workflow |
| `transition` | User resolved a transition surface | Transition lookup → next transition or spawn workflow |
| `run_complete` | A workflow run finished | JourneyOrchestrator → next journey step group |
| `refinement` | User requested a change against a prior artifact | re-entry policy helper → new session phase |
| `decision` | User resolved a pending harness decision | Clear `awaiting_decision` → continue selected path |
| `resume` | Client reconnect / session reload | Reload Session → re-wire transport → continue active run |

---

## Transition Surfaces

Transition surfaces are router-driven UI or no-UI routing moments. They should be
declared minimally and rendered by registered React components when needed.

Recommended direction:

```json
{
  "id": "coding_journey_selector",
  "transition_type": "user_choice_context",
  "ui": { "component": "CodingJourneySelector", "mode": "screen" },
  "options": [
    { "id": "autonomous", "route_to": "DesignDocs", "context_variables": { "design_docs_hitl": false } },
    { "id": "guided", "route_to": "DesignDocs", "context_variables": { "design_docs_hitl": true } }
  ]
}
```

Important:

- Single-route transitions use `route_to`; user-choice transitions use `options[].route_to`
- `ui.component` is a renderer name resolved from the UI registry
- transition components emit `option_id`; SessionRouter owns option resolution
- transition declarations may seed `options[].context_variables`
- target workflow creation filters transition context against declared `context_variables.yaml` keys
- richer visuals belong in the component implementation, not in the generic routing schema

Programmatic workflow starts should use `mozaiksai.core.session.launcher` so they still pass through SessionRouter validation, dependency rerouting, context filtering, and chat-session binding.

Recommended transition types:

| Transition type | Purpose | UI required |
|---|---|---|
| `user_choice` | User chooses one of multiple paths | Yes |
| `user_choice_context` | User chooses context while continuing to a shared target | Yes |
| `user_choice_route` | User chooses between explicit workflow/transition targets | Yes |
| `confirm` | User approves or rejects a route | Yes |
| `progress_view` | Show progress for backend-only work | Yes |
| `prerequisite_redirect` | Explain why router is redirecting to an unmet prerequisite | Optional |
| `silent` | No UI, just continue | No |

SessionRouter should decide whether to surface a transition at all.

Examples:

- direct start blocked by dependency -> `prerequisite_redirect` or `silent`
- DesignDocs runs invisibly -> `silent` or `progress_view`
- branch selection -> `user_choice`

---

## Session State Model

One `Session` record per `(app_id, user_id, journey_instance)`.

```python
@dataclass
class Session:
    session_id: str                  # unique
    app_id: str
    user_id: str

    # Journey position
    journey_key: str                 # which journey definition (e.g. "build")
    journey_position: int            # index into journey.steps (normalized step groups)
    journey_total_steps: int

    # Builder-sequence status
    sequence_status: SequenceStatus
    # in_progress | completed | stale | revising
    sequence_completed_at: datetime | None
    active_revision_id: str | None
    active_change_request_id: str | None
    current_revision_scope: str | None      # patch | design | feature | core
    revision_origin_workflow: str | None
    restart_from_workflow: str | None

    # Lifecycle
    lifecycle_state: SessionLifecycle
    # initial | active | awaiting_transition | awaiting_decision | completed | stale

    # Active execution
    current_run_id: str | None       # chat_id of the currently executing workflow run
    current_workflow_name: str | None
    active_mfj_run_ids: list[str]    # parallel run ids when in a parallel step group

    # Blocked state
    pending_transition_id: str | None
    pending_harness_decision: PendingHarnessDecision | None
    # persisted deferred decision envelope:
    # {
    #   decision_id, decision_type, message, rationale, confidence,
    #   recommended_workflow_id, selected_paths, clarification_question,
    #   change_request_id, revision_id,
    #   trigger_source, requested_workflow_id, journey_id,
    #   context_variables, trigger_payload,
    #   actions[], metadata, created_at
    # }

    # Artifact layer refs
    artifact_version_refs: dict[str, str]
    # keys: "concept" | "design_docs" | "workflow_bundle" | "app_bundle"
    # values: artifact_version_id of the latest committed version per layer
    stale_layers: dict[str, str]
    # keys: artifact layer names; values: invalidation reasons or revision ids

    # Revision history
    revision_history: list[RevisionEntry]
    # [{revision_id, change_request_id, scope, origin_workflow, from_version_refs, to_version_refs, timestamp}]

    created_at: datetime
    updated_at: datetime
```

```python
class SequenceStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    STALE = "stale"
    REVISING = "revising"
```

```python
class SessionLifecycle(str, Enum):
    INITIAL = "initial"
    ACTIVE = "active"
    AWAITING_TRANSITION = "awaiting_transition"
    AWAITING_DECISION = "awaiting_decision"
    COMPLETED = "completed"
    STALE = "stale"             # upstream artifact invalidated by a core change
```

### Lifecycle Transitions

```
initial
  → active                 (first workflow started)
  → awaiting_transition    (entry transition presented)

awaiting_transition
  → active                 (transition resolved → workflow spawned)
  → awaiting_transition    (transition resolved → next transition)

active
  → active                 (journey auto-advance spawned next step)
  → awaiting_transition    (router surfaces a transition)
  → awaiting_decision      (runtime persisted a deferred harness decision)
  → active                 (refinement trigger resolved to a workflow start)
  → completed              (last journey step completed)

awaiting_decision
  → active                 (user resolved the pending harness decision)

completed
  → active                 (post-completion refinement request resolves to a workflow start)
  → stale                  (core change invalidated upstream layer)

stale
  → active                 (new concept revision started ValueEngine)
```

Important:

- `lifecycle_state` answers "what is the router/execution doing right now?"
- `sequence_status` answers "what is the build journey's revision state?"
- refinement does **not** need a separate `REFINING` lifecycle enum if
  `sequence_status=revising` is persisted alongside `lifecycle_state=active`

Examples:

- user is mid-build in `AppGenerator`, asks for a core change:
  `lifecycle_state=active`, `sequence_status=revising`
- full build finished, user asks for a patch:
  `lifecycle_state=active`, `sequence_status=revising`, prior
  `sequence_completed_at` stays populated
- core revision invalidates downstream layers before rerun:
  `sequence_status=stale`, then `revising` once the new `ValueEngine` run starts

---

## How SessionRouter Consults GlobalPackGraph

GlobalPackGraph is the static source of truth for topology. SessionRouter never
mutates it — it only reads.

### Session Start

```
1. load_global_pack_graph()
2. resolve requested workflow or requested journey
3. enforce hard dependencies before starting anything
4. if router wants a transition surface → set awaiting_transition
5. else start the first valid workflow immediately
```

### Dependency Enforcement

Dependencies are checked before direct workflow starts and before router-driven re-entry.

```
1. user requests workflow W
2. load W.dependencies
3. find the first unmet required dependency D
4. if none -> continue to W
5. if D exists:
     - either silently reroute to D
     - or emit a prerequisite transition surface for D
```

Dependencies are the gating truth. Journeys do not need to act as a second hard
prerequisite system.

### Transition Resolution

```
1. load transition declaration by pending_transition_id
2. find the selected option_id
3. if route_to is another transition id -> remain in awaiting_transition
4. if route_to is a workflow id -> enforce dependencies, then spawn workflow run
5. update Session.pending_transition_id, Session.current_workflow_name, Session.current_run_id
```

### Journey Auto-Advance (delegated from JourneyOrchestrator)

```
1. JourneyOrchestrator fires on run_complete as today
2. SessionRouter receives the completion event first
3. increments Session.journey_position
4. checks whether the next phase needs a transition surface
5. if no transition is needed -> delegates spawn to JourneyOrchestrator
6. updates Session.current_workflow_name, Session.current_run_id
```

### Parallel Step Groups (MFJ-adjacent)

When `journey.steps` contains a parallel group such as
`["ThemeCapture", "ExistingAppDiscovery"]`:

```
1. SessionRouter spawns both runs (via JourneyOrchestrator as today)
2. records both run ids in Session.active_mfj_run_ids
3. remains in ACTIVE until all sibling runs complete
4. JourneyOrchestrator's existing sibling-check logic handles the "advance only when
   all siblings done" barrier — SessionRouter only adds the state write-back
```

Parallel groups must not contain required dependency edges between siblings. If
`AppGenerator` depends on `AgentGenerator`, they must be authored as separate
serial steps.

MFJ (within a single workflow) is invisible to SessionRouter. SessionRouter only sees
the outer workflow's run_complete event.

---

## How SessionRouter Invokes Refinement Re-Entry

```
1. trigger_source == "refinement" arrives at /api/workflows/trigger
2. SessionRouter loads Session for (app_id, user_id) plus accepted artifact refs
3. derives the re-entry workflow from change class + app-declared control-plane routing
4. receives RoutingDecision(workflow_id, context_seed, is_full_restart)
5. control plane persists artifact-level stale status for affected
   artifact_version_refs using the new `change_request_id`
6. if the harness defers launch and returns a decision UI first:
     - persist active_revision_id and active_change_request_id immediately
     - keep the current chat/workflow binding until a new run is launched
     - persist the full pending harness decision in SessionRouterState
     - set Session.lifecycle_state = AWAITING_DECISION
     - when the follow-up action is submitted, reuse the persisted
       `change_request_id` and `revision_id`
7. if is_full_restart:
     - allocate active_revision_id and active_change_request_id
     - mark Session.sequence_status = STALE
     - mark downstream artifact_version_refs as stale in stale_layers
     - transition to new ValueEngine revision run
     - set Session.sequence_status = REVISING
8. else:
     - allocate active_revision_id and active_change_request_id
     - set Session.sequence_status = REVISING
     - spawn re-entry workflow with context_seed merged into context_variables
     - append RevisionEntry to Session.revision_history
9. emit session.revision_requested
10. emit session.reentry_selected
11. emit session.revision_started when the workflow run is bound
```

Refinement re-entry creates a **new workflow run** against the same Session.
The Session retains its journey_position — refinement is not a journey step.
On completion, Session transitions back to ACTIVE or COMPLETED depending on prior state.

Rules:

- build completion affects decision shaping, not semantic classification
- a `core` reroute to `ValueEngine` is still revision mode, not blank-slate
  intake
- the workflow receives a revision context containing prior concept, design,
  artifact lineage, change request, and impact metadata

## Revision Context Contract

Every workflow re-entry should receive the same revision envelope, regardless
of whether the session was still mid-build or already completed:

```python
{
    "build_mode": "revision",
    "revision_scope": "patch" | "design" | "feature" | "core",
    "revision_id": "...",
    "change_request_id": "...",
    "artifact_kind": "...",
    "artifact_version_id": "...",
    "revision_origin_workflow": "...",
    "sequence_status": "in_progress" | "completed" | "stale" | "revising",
    "refinement_request": "...",
    "refinement_request_meta": {...},
    "change_intent": {...},
    "impact_set": {...},
}
```

This contract exists so:

- `ValueEngine` can reopen the concept with awareness of prior decisions
- `DesignDocs` can revise design artifacts without pretending the build is new
- `AgentGenerator` and `AppGenerator` can decide between targeted edits and
  wider regeneration
- downstream workflows can detect stale layers without scraping transcript
  history

## Session Events

SessionRouter should emit explicit revision-aware session events:

- `session.started`
- `session.phase_advanced`
- `session.awaiting_transition`
- `session.sequence_completed`
- `session.revision_requested`
- `session.reentry_selected`
- `session.layers_invalidated`
- `session.revision_started`
- `session.revision_completed`
- `session.completed`
- `session.stale`

---

## Migration Path From Current JourneyOrchestrator

The current `JourneyOrchestrator` embeds session position in individual chat docs
(`journey_id`, `journey_key`, `journey_step_index`). This is the bootstrapping state
that SessionRouter formalizes.

Migration is **additive**, not a rewrite:

1. SessionRouter is introduced as a new layer in `mozaiksai/core/session/`.
2. `JourneyOrchestrator.handle_run_complete` is wrapped — it reports to SessionRouter
   before/after its current logic.
3. `/api/workflows/trigger` routes all incoming triggers through SessionRouter.
4. The legacy `journey_id` / `journey_step_index` fields on chat docs are kept as
   fallback until Session records are fully backfilled.
5. Once Session records are the canonical source, the legacy fields can be cleaned up.

---

## File Location

```
mozaiksai/core/session/
├── __init__.py          # exports SessionRouter, Session, SessionLifecycle, SequenceStatus
├── router.py            # SessionRouter class — decision logic
├── model.py             # Session, SessionLifecycle, SequenceStatus, RevisionEntry dataclasses
└── persistence.py       # Session CRUD against MongoDB (sessions collection)
```

SessionRouter is a runtime primitive. It belongs in `mozaiksai/core/`, not in any
platform workflow directory.

---

## Open Questions (pre-implementation)

1. **Multi-device / multi-tab**: Can two clients share one Session record, or is Session
   per-connection? Recommended: per `(app_id, user_id)` scope, transport re-wired on
   reconnect.

2. **Parallel journeys**: Can a user run `build` and a standalone `AgentGenerator` in
   parallel? Recommend: one active journey per `app_id` scope; standalone workflows get
   a synthetic journey with one step.

3. **Pending decision protocol**: the runtime now persists a replayable
   `pending_harness_decision` and the shell restores it from
   `chat_meta.session_state`. The remaining open question is whether future
   `clarify_scope` flows should support freeform text replies in addition to
   action buttons.

4. **Transition declarations**: `GlobalPackGraph` now uses `entrypoints[]` for
   shell entry routes and `transitions[]` for router decisions. Sequence metadata
   does not own entry UI.
