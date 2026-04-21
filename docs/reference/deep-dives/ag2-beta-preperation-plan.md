# AG2 Beta Preperation Plan
ag2-beta-preperation-plan
**Status:** Active implementation plan
**Last updated:** 2026-04-02
**Depends on:** [AG2 Beta & GroupChat Strategy](ag2-beta-groupchat-strategy.md), [Orchestration and Decomposition](../../architecture/orchestration-and-decomposition.md), [Mid-Flight Journeys](mid-flight-journeys.md)

---

## Purpose

This document turns the near-term groupchat event strategy into a concrete
file-by-file implementation plan for the current runtime.

The goal is not to replace the groupchat engine. The goal is to make the
existing event-iteration runtime explicit, normalized, and easier to evolve.

---

## Verified Starting Point

The current runtime already contains several pieces of the target model.

- `orchestration_patterns.py` already uses `a_run_group_chat_iter(...)`
- `workflow/stream/processor.py` already runs a handler-based event loop
- `workflow/stream/handlers/mozaiks_event_handler.py` already bridges AG2 custom
  events into the dispatcher
- `events/ag2_events.py` already defines AG2-native custom runtime events
- `events/ag2_event_bridge.py` already maps custom AG2 events into dispatcher
  event names

This means the next step is a convergence refactor, not a greenfield runtime
rewrite.

---

## Current Gaps

The runtime still has multiple competing event vocabularies.

### Gap 1. Mixed event namespaces

The runtime currently mixes event families such as:

- `chat.agent_output_validated`
- `chat.decomposition_planned`
- `chat.run_complete`
- `runtime.agent_thinking`
- `mfj.journey_started`
- websocket-facing `chat.text`, `chat.tool_call`, and similar payload types

These are all useful, but they are not yet one deliberate runtime contract.

### Gap 2. UI payload creation still leaks runtime semantics

Some handlers construct UI-facing payloads directly, while other code paths emit
dispatcher events first and let transport/projectors infer the final frontend
shape.

That keeps the runtime working, but it makes it harder to reason about which
event is canonical.

### Gap 3. Custom AG2 events and validated text outputs overlap

Structured outputs and decomposition signals can currently enter the runtime by
more than one route:

- validated `TextEvent` processing in stream handlers
- explicit AG2 custom events through `ag2_events.py`

Both routes are legitimate today, but they should converge on one normalized
runtime event profile above the engine.

### Gap 4. Transport still reacts to legacy chat event names

The transport and dispatcher still depend on event names such as
`chat.run_complete`. That is acceptable in the short term, but the runtime
should stop inventing those names ad hoc in multiple places.

---

## Target End State

The runtime should have three explicit layers.

### Layer 1. Engine events

AG2 built-in events and AG2 custom events remain engine-native and are consumed
inside the stream processor.

### Layer 2. Normalized runtime events

The runtime emits one stable event vocabulary for orchestration semantics, such
as:

- `process.started`
- `process.paused`
- `process.completed`
- `process.failed`
- `task.started`
- `task.awaiting_input`
- `chat.message_appended`
- `chat.tool_call_requested`
- `chat.tool_result_received`
- `runtime.decomposition_planned`
- `runtime.fan_out_requested`
- `runtime.fan_in_ready`
- `artifact.updated`

### Layer 3. Transport projection

WebSocket payload types remain a UI concern. They should be projected from the
normalized runtime events rather than assembled independently inside multiple
handlers.

---

## Implementation Phases

## Phase 1: Stabilize The Event Boundary

**Goal:** make the current event iteration path the only authoritative execution
path.

### Files

- `mozaiksai/core/workflow/orchestration_patterns.py`
- `mozaiksai/core/workflow/stream/processor.py`
- `mozaiksai/core/workflow/stream/handlers/__init__.py`

### Changes

- keep `a_run_group_chat_iter(...)` as the execution primitive
- make the stream processor the canonical place where AG2 events are observed
- remove any remaining assumptions that orchestration meaning should be inferred
  after the stream finishes
- ensure every execution-relevant AG2 event family is handled through the
  registry or an explicit default path

### Exit Criteria

- one clear event-processing path per run
- no new runtime semantics added outside the stream processor path

---

## Phase 2: Introduce A Canonical Runtime Event Profile

**Goal:** give the runtime one engine-agnostic event vocabulary above AG2.

### Files

- `mozaiksai/core/events/ag2_event_bridge.py`
- `mozaiksai/core/events/event_serialization.py`
- new module under `mozaiksai/core/events/` for normalized runtime event names
  and mapping helpers

### Changes

- define the canonical runtime event families and payload shape
- centralize mapping from AG2 built-in events and AG2 custom events into that
  profile
- stop scattering event-name decisions across handlers
- keep `event_serialization.py` focused on payload normalization and transport
  projection, not ownership of runtime semantics

### Exit Criteria

- one module defines canonical runtime event names
- both built-in AG2 events and custom AG2 events map through the same profile
- downstream code consumes normalized runtime events, not raw AG2 naming

---

## Phase 3: Converge Custom Checkpoint Events With Existing Text-Based Signals

**Goal:** remove ambiguity between custom AG2 checkpoint events and text-derived
runtime signals.

### Files

- `mozaiksai/core/events/ag2_events.py`
- `mozaiksai/core/workflow/stream/handlers/mozaiks_event_handler.py`
- `mozaiksai/core/workflow/stream/handlers/text_handler.py`

### Changes

- add the first explicit checkpoint events needed by pack and build workflows:
  decomposition, fan-out readiness, fan-in readiness, resume checkpoint,
  approval checkpoint, and artifact readiness
- keep text-based validation and decomposition extraction as a compatibility
  source for now
- normalize both sources into the same runtime event family before they reach
  orchestration listeners
- prefer explicit AG2 custom events for new workflow semantics going forward

### Exit Criteria

- pack and auto-tool flows can react to one normalized event family
- explicit checkpoint events exist for new build/runtime milestones
- text-derived checkpoints remain transitional, not canonical

---

## Phase 4: Move Dispatcher Registrations To Normalized Runtime Events

**Goal:** make orchestration listeners depend on the runtime contract instead of
legacy chat-specific names.

### Files

- `mozaiksai/core/events/unified_event_dispatcher.py`
- `mozaiksai/core/events/auto_tool_handler.py`
- `mozaiksai/core/workflow/pack/workflow_pack_coordinator.py`
- `mozaiksai/core/workflow/pack/journey_orchestrator.py`

### Changes

- register pack, journey, and auto-tool handlers against the canonical runtime
  event names
- preserve compatibility aliases only if they are required during the
  transition, and keep them localized to the dispatcher boundary
- stop encoding orchestration policy in transport-oriented event names

### Exit Criteria

- pack and journey react to normalized runtime events
- legacy event-name aliases, if any, are isolated and temporary

---

## Phase 5: Make Transport A Pure Projector

**Goal:** keep the frontend stream stable while removing orchestration policy
from the transport layer.

### Files

- `mozaiksai/core/transport/simple_transport.py`
- `mozaiksai/core/transport/workflow_bridge.py`
- `mozaiksai/core/events/unified_event_dispatcher.py`

### Changes

- project websocket event envelopes from normalized runtime events
- keep frontend event types stable where possible
- prevent transport from being the place where MFJ, completion, or orchestration
  semantics are inferred

### Exit Criteria

- transport consumes normalized runtime events
- UI event types are projections, not orchestration triggers

---

## Phase 6: Add Focused Regression Tests

**Goal:** lock the event contract before larger runtime cleanup.

### Existing Tests To Extend

- `tests/test_event_dispatcher_domain_event.py`
- `tests/test_workflow_pack_coordinator_mfj.py`

### New Test Areas

- normalization of AG2 built-in events into canonical runtime events
- normalization of AG2 custom checkpoint events into canonical runtime events
- dispatcher alias behavior during transition
- pack coordinator reaction to normalized decomposition and completion events
- transport projection from normalized runtime event to websocket envelope

### Exit Criteria

- the normalized runtime event profile is covered directly by tests
- MFJ triggers and run-complete semantics are no longer protected only by
  incidental UI-path tests

---

## Recommended First Code Change

The first code change should not be a broad rename.

The best first implementation slice is:

1. add one canonical runtime-event mapping helper module under
   `mozaiksai/core/events/`
2. route `StructuredOutputEvent`, text-derived validated outputs, and
   decomposition signals through that helper
3. make `UnifiedEventDispatcher` register MFJ listeners on the new normalized
   event names while preserving localized compatibility aliases

That slice improves the runtime immediately without destabilizing transport or
resume behavior.

---

## Non-Goals For This Step

- replacing AG2 groupchat execution
- migrating the runtime to AG2 beta streams now
- rewriting pack or journey semantics
- changing the durable control-plane model

This plan is intended to clean the current runtime around its real execution
boundary, not to introduce a second migration at the same time.
