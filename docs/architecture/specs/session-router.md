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
control-plane contracts now live under `mozaiksai/core/control_plane/`, while
the first-party factory implementation is still injected by the Studio/Mozaiks
host into SessionRouter through a trigger-route resolver seam. The runtime does
not import factory policy directly.

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
| `approval` | Human approval callback received | Unblock `awaiting_approval` → resume queued workflow |
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

    # Lifecycle
    lifecycle_state: SessionLifecycle
    # initial | active | awaiting_transition | awaiting_approval | completed | stale

    # Active execution
    current_run_id: str | None       # chat_id of the currently executing workflow run
    current_workflow_name: str | None
    active_mfj_run_ids: list[str]    # parallel run ids when in a parallel step group

    # Blocked state
    pending_transition_id: str | None
    pending_approval_id: str | None  # set when lifecycle_state == awaiting_approval

    # Artifact layer refs
    artifact_version_refs: dict[str, str]
    # keys: "concept" | "design_docs" | "workflow_bundle" | "app_bundle"
    # values: artifact_version_id of the latest committed version per layer

    # Refinement history
    refinement_history: list[RefinementEntry]
    # [{change_request_id, artifact_kind, from_version_id, to_version_id, timestamp}]

    created_at: datetime
    updated_at: datetime
```

```python
class SessionLifecycle(str, Enum):
    INITIAL = "initial"
    ACTIVE = "active"
    AWAITING_TRANSITION = "awaiting_transition"
    AWAITING_APPROVAL = "awaiting_approval"
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
  → awaiting_approval      (workflow emitted approval_required event)
  → active                 (refinement trigger resolved to a workflow start)
  → completed              (last journey step completed)

awaiting_approval
  → active                 (approval received → resume)

completed
  → active                 (post-completion refinement request resolves to a workflow start)
  → stale                  (core change invalidated upstream layer)

stale
  → active                 (new concept revision started ValueEngine)
```

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
2. SessionRouter loads Session for (app_id, user_id)
3. derives the re-entry workflow from change class + artifact ownership
4. receives RoutingDecision(workflow_id, context_seed, is_full_restart)
5. if is_full_restart:
     - mark Session.lifecycle_state = STALE
     - mark downstream artifact_version_refs as stale
     - transition to new ValueEngine run → lifecycle_state = ACTIVE
6. else:
     - transition Session.lifecycle_state = REFINING
     - spawn re-entry workflow with context_seed merged into context_variables
     - append RefinementEntry to Session.refinement_history
7. emit session.refinement_entered event
```

Refinement re-entry creates a **new workflow run** against the same Session.
The Session retains its journey_position — refinement is not a journey step.
On completion, Session transitions back to ACTIVE or COMPLETED depending on prior state.

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
├── __init__.py          # exports SessionRouter, Session, SessionLifecycle
├── router.py            # SessionRouter class — decision logic
├── model.py             # Session, SessionLifecycle, RefinementEntry dataclasses
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

3. **Approval protocol**: `awaiting_approval` is declared in the model but the approval
   event contract is not yet specified. That belongs in a separate approval-flow spec
   before SessionRouter.persistence.py is implemented.

4. **Transition declarations**: `GlobalPackGraph` now uses `entrypoints[]` for
   shell entry routes and `transitions[]` for router decisions. Sequence metadata
   does not own entry UI.
