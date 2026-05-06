# AG2 Beta & GroupChat Strategy

**Status:** Proposed near-term strategy
**Last updated:** 2026-04-02
**Depends on:** [Event System](../../architecture/foundations/event-system.md), [Event Contracts](../../architecture/foundations/event-contracts.md), [Orchestration and Decomposition](../../architecture/orchestration-and-decomposition.md), [Orchestration Control Loops](../../architecture/foundations/orchestration-control-loops.md), [Mid-Flight Journeys](mid-flight-journeys.md), [Implementation Plan](ag2-beta-preperation-plan.md)

---

## Purpose

This document is an engine-strategy note, not the canonical control-plane
contract. The authoritative ownership model for workflow runs, builder sessions,
and refinement workers is
[Orchestration Control Loops](../../architecture/foundations/orchestration-control-loops.md).

This document defines the near-term runtime strategy for Mozaiks while the
platform continues to use AG2 group chats and `a_run_group_chat`-style
execution.

The goal is to keep the current execution engine while removing as much
transcript guessing and post-hoc orchestration inference as possible.

The core move is:

- keep group chat execution
- consume AG2 run events explicitly
- emit custom runtime checkpoints when workflow semantics matter
- normalize those events behind the adapter boundary
- let Mozaiks orchestration react to normalized runtime events, not transcript
  archaeology

This keeps the current system operational while aligning with a future
stream-first execution model.

---

## Non-Negotiable Rules

- Group chat is the current execution engine, not the long-term architectural
  center of the runtime.
- Runtime orchestration must react to explicit events, not fragile transcript
  parsing.
- The control plane remains external to the group chat transcript.
- App domain events, control-plane facts, and workflow runtime events remain
  separate layers.
- All engine-specific event handling stays below `OrchestrationPort`.

---

## What Stays The Same

Mozaiks still keeps its existing layer boundaries:

- app/backend facts remain domain events
- control-plane state remains durable session-routing state
- workflow runtime events remain live execution signals
- pack and journey logic remain runtime policy above the engine

That means the following runtime responsibilities remain Mozaiks-owned:

- workflow routing
- prerequisite gating
- MFJ contract evaluation
- journey progression
- transport projection
- persistence of canonical session state

---

## What Changes

The execution adapter should stop treating group chat as a black box.

Instead of:

1. running the chat to completion
2. reading transcript or side effects afterward
3. inferring what happened

the adapter should:

1. iterate AG2 group chat events as they occur
2. emit normalized runtime events immediately
3. react to custom checkpoints deterministically
4. let upper runtime layers decide fan-out, fan-in, pause, approval, and resume

This makes the runtime event-driven even while the engine remains group chat.

---

## AG2 Patterns To Use

Two AG2 notebook patterns are the basis for this strategy.

### 1. Iterating group chat runs

Use AG2 run iteration for group chat execution.

The relevant pattern is:

- `run_group_chat_iter()`
- `yield_on=[...]`
- one event at a time
- caller-controlled loop

This enables:

- stepwise observability
- pre-execution gating
- approval pauses
- early abort on unsafe or invalid tool requests
- deterministic reaction to workflow checkpoints

### 2. Custom events

Use AG2 custom events for runtime-specific checkpoints that are not expressed
cleanly by built-in AG2 events.

This enables:

- decomposition checkpoints
- validation checkpoints
- artifact publication checkpoints
- explicit pause/resume hints
- milestone events for generator/build workflows

These events should be runtime-facing execution events, not app domain events.

---

## Raw AG2 Events To Consume

At the adapter layer, the runtime should explicitly consume these AG2 event
families.

### Built-in event families

| AG2 event | Runtime meaning |
| --- | --- |
| `GroupChatRunChatEvent` | a speaker turn is starting |
| `TextEvent` | a message was produced |
| `ToolCallEvent` | an agent wants to execute a tool |
| `ToolResponseEvent` | tool returned a result |
| `ExecutedFunctionEvent` | tool/function execution completed |
| `InputRequestEvent` | runtime needs user input |
| `TerminationEvent` | termination condition triggered |
| `RunCompletionEvent` | run fully completed |
| `ErrorEvent` | execution failure surfaced |

### Consumption rule

The runtime should observe these events at execution time, not reconstruct them
later from persisted transcript rows.

---

## Custom Events Mozaiks Should Add

Mozaiks should define explicit runtime checkpoint events for workflow semantics
that matter above the engine.

Recommended custom event families:

### Decomposition and MFJ

- `DecompositionPlannedEvent`
- `FanOutRequestedEvent`
- `FanInReadyEvent`
- `ResumeCheckpointEvent`

### Validation and approvals

- `ValidationCheckpointEvent`
- `ApprovalRequestedEvent`
- `ApprovalResolvedEvent`

### Artifact lifecycle

- `ArtifactDraftedEvent`
- `ArtifactPublishedEvent`
- `ArtifactBundleReadyEvent`

### Generator/build workflow checkpoints

- `AppSpecCreatedEvent`
- `BuildPlanCreatedEvent`
- `BuildTaskStartedEvent`
- `BuildTaskCompletedEvent`
- `BuildReviewReadyEvent`

These should be emitted from tools, hooks, or orchestration helpers when the
runtime crosses a meaningful workflow checkpoint.

---

## Normalized Runtime Events Mozaiks Should Emit

The AG2 adapter should convert raw AG2 events and custom events into a stable
runtime-facing event vocabulary.

Recommended normalized families:

### Process

- `process.started`
- `process.paused`
- `process.completed`
- `process.failed`

### Task

- `task.started`
- `task.progress`
- `task.completed`
- `task.failed`
- `task.awaiting_input`

### Chat

- `chat.message_appended`
- `chat.tool_call_requested`
- `chat.tool_result_received`
- `chat.handoff_requested`
- `chat.run_complete`

### UI tools

- `ui.tool.requested`
- `ui.tool.responded`
- `ui.tool.completed`

### Artifacts

- `artifact.created`
- `artifact.updated`
- `artifact.ready`

### Runtime control hints

- `runtime.decomposition_planned`
- `runtime.fan_out_requested`
- `runtime.fan_in_ready`
- `runtime.resume_requested`

The exact namespace may evolve, but the contract should be normalized and
engine-agnostic above the adapter.

---

## Event Mapping Guidance

### AG2 built-in to normalized runtime event

| AG2 event | Emit |
| --- | --- |
| `GroupChatRunChatEvent` | `task.started` or `task.progress` |
| `TextEvent` | `chat.message_appended` |
| `ToolCallEvent` | `chat.tool_call_requested` |
| `ToolResponseEvent` | `chat.tool_result_received` |
| `InputRequestEvent` | `task.awaiting_input` and `ui.tool.requested` when applicable |
| `TerminationEvent` | `process.paused` or `process.completed`, depending on termination reason |
| `RunCompletionEvent` | `chat.run_complete` and `process.completed` |
| `ErrorEvent` | `process.failed` |

### AG2 custom to normalized runtime event

| Custom event | Emit |
| --- | --- |
| `DecompositionPlannedEvent` | `runtime.decomposition_planned` |
| `ValidationCheckpointEvent` | `task.progress` or `runtime.validation_checkpoint` |
| `ArtifactPublishedEvent` | `artifact.ready` |
| `BuildPlanCreatedEvent` | `runtime.build_plan_created` |

---

## How MFJ Should Work Under This Strategy

MFJ should stop depending primarily on transcript-derived structured output
discovery.

Near-term runtime contract:

1. a decomposition-capable agent produces structured output
2. runtime or tool helper emits `DecompositionPlannedEvent`
3. adapter normalizes that to `runtime.decomposition_planned`
4. `WorkflowPackCoordinator` reacts to that normalized event
5. fan-out happens deterministically using the existing pack graph
6. child completion still feeds fan-in and parent resume through the current
   pack runtime

This preserves the current MFJ architecture while making the trigger boundary
explicit.

### Important rule

MFJ trigger semantics should be driven by a runtime checkpoint event, not by
scanning text messages or loosely parsing agent chatter.

---

## How Pack And Journey Should React

### WorkflowPackCoordinator

Should react to:

- `runtime.decomposition_planned`
- `chat.run_complete`
- optional future `runtime.fan_in_ready`

Should not rely on:

- arbitrary text transcripts
- presenter wording conventions
- implicit completion inferred from side effects alone

### JourneyOrchestrator

Should react to:

- `chat.run_complete`
- normalized `process.completed`

It should remain above the engine and should not need AG2-specific event types.

### UniversalOrchestrator

Should continue to react to typed routing inputs and control-plane decisions,
not raw group-chat details.

---

## Persistence Guidance

While group chat remains the engine, persistence should move toward projections
instead of transcript primacy.

Persist canonically:

- session state
- control-plane state
- normalized runtime events
- artifact state
- structured outputs that matter for resume or replay

Treat raw transcript history as:

- replay support
- debugging material
- UI rendering input

Do not treat transcript shape as the canonical execution contract.

---

## Recommended Adapter Loop Shape

The execution adapter should conceptually behave like this:

```text
start group chat
  -> iterate AG2 events
  -> map built-in events to normalized runtime events
  -> map custom runtime checkpoints to normalized runtime events
  -> dispatch normalized events immediately
  -> allow gating, pause, or abort decisions during iteration
  -> finish with process and run completion events
```

This is the critical behavioral change.

The adapter is no longer a thin wrapper around a black-box group chat run.
It becomes a real execution-engine adapter.

---

## Why This Aligns With The Future

This approach keeps the current engine but adopts a stream-oriented mindset.

That helps future migration because:

- runtime policy above the adapter becomes less AG2-specific
- pack and journey react to normalized events, not transcript quirks
- transport consumes stable event families
- custom runtime checkpoints already exist when Beta task or stream adapters are
  introduced later

In other words:

- current engine: AG2 group chat
- current runtime style: explicit event iteration
- future engine: Beta task or stream adapter
- future runtime style: unchanged normalized event contract

---

## Summary

- Keep AG2 group chats for now.
- Use AG2 run iteration instead of black-box execution wherever possible.
- Emit custom events for workflow checkpoints that matter to the runtime.
- Normalize built-in and custom AG2 events into stable runtime event families.
- Let MFJ, pack, journey, and transport react to normalized runtime events.
- Keep the control plane outside the transcript and outside engine-specific
  event shapes.

This is the cleanest way to stabilize the current runtime without committing the
platform to legacy group chat as its permanent architecture.
