# Universal Orchestrator

The old "universal orchestrator" idea has been folded into three explicit
runtime surfaces:

- `SessionRouter` decides which execution context should receive a user event.
- The Refinement Engine classifies build/refinement intent against durable artifact
  state.
- `OrchestrationPort` starts, re-enters, or cancels a concrete workflow run.
  In the current runtime, re-entry means Mozaiks restores runtime-owned session
  routing plus canonical AG2 run-stream events; the installed AG2 Network API
  does not expose durable channel resume.

There is no global agent mesh and no workflow-local router that owns product
intent. Workflow-local handoffs stay inside one workflow bundle and compile to
AG2 1.0 beta Network `TransitionGraph` objects.

## Current Shape

```text
User / API / UI event
  -> SessionRouter
      -> direct module/action route
      -> Refinement Engine refinement route
      -> workflow run route
  -> OrchestrationPort.run/resume/cancel
  -> AG2 1.0 beta workflow execution
  -> runtime events + artifact persistence
```

## Ownership

`SessionRouter` owns route selection across execution contexts. It should not
know the internals of individual agents.

The Refinement Engine owns builder-session interpretation:

- refinement classification
- artifact scope
- context graph scope
- checkpoint and confirmation decisions
- coding-worker request preparation

`OrchestrationPort` owns the runtime execution boundary. Everything above it is
engine-agnostic; the AG2 adapter owns AG2 1.0 beta agent, stream, and Network
translation details.

Current implementation note:

- `SessionRouter.resolve_resume(...)` decides which persisted chat/run should
  continue for the app/user scope.
- `ChatSessions` stores run metadata, usage state, artifact projection, and
  session/journey correlation for that concrete chat/run.
- AG2 run history is the canonical execution-state record for one workflow run,
  stored through a persistent `MemoryStream` backed by runtime-owned stream
  storage keyed per `app_id + chat_id`.
- `OrchestrationPort.resume(...)` re-enters the workflow using that persisted
  AG2 event history plus runtime-managed session routing state.
- This is separate from Refinement Engine resume, which is builder-session
  continuity over artifacts, checkpoints, and routing decisions.

Workflow bundles own local execution structure:

- `agents.yaml`
- `transition_graph.yaml`
- `context_variables.yaml`
- `tools.yaml`
- `structured_outputs.yaml`
- `middleware.yaml`
- `ui_config.yaml`

## Routing Rules

Use the smallest routing layer that owns the decision:

| Decision | Owner |
| --- | --- |
| Which active session should receive this event? | `SessionRouter` |
| Is this a refinement, patch, rebuild, or new build? | Refinement Engine |
| Which workflow sequence should execute? | `extension_registry.json` + Refinement Engine route |
| Which agent speaks next inside one workflow? | `transition_graph.yaml` compiled to AG2 `TransitionGraph` |
| Which UI state should the websocket show? | Runtime transport + `ui_config.yaml` |

Do not encode product-level route decisions in workflow-local handoffs. A
workflow-local transition can read deterministic context variables, tool results,
or typed structured-output state. Natural-language intent classification belongs
in the Refinement Engine before the workflow run is started or resumed.

For AG2 1.0 beta specifically, be careful with the word `resume`: AG2 typed events
are the runtime source of truth, while durable AG2 Network channel continuation
is not available in the installed API. Mozaiks restores runtime-owned session
routing state plus persistent AG2 run-stream history first and then re-enters
the workflow through `OrchestrationPort`.

## AG2 1.0 beta Mapping

Mozaiks keeps workflow YAML as its authoring contract and compiles it to AG2 1.0 beta
runtime objects:

| Mozaiks contract | AG2 1.0 beta runtime object |
| --- | --- |
| `agents.yaml` | `Agent` registration |
| `transition_graph.yaml` | `TransitionGraph` |
| `context_variables.yaml` | workflow context variables |
| `tools.yaml` | agent tools and typed routing outputs |
| run persistence | persistent `MemoryStream` + runtime AG2 stream storage |
| runtime events | AG2 stream events normalized into Mozaiks domain events |

This keeps generator output deterministic while allowing the AG2 execution
backend to evolve underneath `OrchestrationPort`.

