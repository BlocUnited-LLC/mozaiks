# Universal Orchestrator

The old "universal orchestrator" idea has been folded into three explicit
runtime surfaces:

- `SessionRouter` decides which execution context should receive a user event.
- The control plane classifies build/refinement intent against durable artifact
  state.
- `OrchestrationPort` starts, resumes, or cancels a concrete workflow run.

There is no global agent mesh and no workflow-local router that owns product
intent. Workflow-local handoffs stay inside one workflow bundle and compile to
AG2 beta Network `TransitionGraph` objects.

## Current Shape

```text
User / API / UI event
  -> SessionRouter
      -> direct module/action route
      -> control-plane refinement route
      -> workflow run route
  -> OrchestrationPort.run/resume/cancel
  -> AG2 beta workflow execution
  -> runtime events + artifact persistence
```

## Ownership

`SessionRouter` owns route selection across execution contexts. It should not
know the internals of individual agents.

The control plane owns builder-session interpretation:

- refinement classification
- artifact scope
- context graph scope
- checkpoint and confirmation decisions
- coding-worker request preparation

`OrchestrationPort` owns the runtime execution boundary. Everything above it is
engine-agnostic; the AG2 adapter owns AG2 beta agent, stream, and Network
translation details.

Workflow bundles own local execution structure:

- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `tools.yaml`
- `structured_outputs.yaml`
- `hooks.yaml`
- `ui_config.yaml`

## Routing Rules

Use the smallest routing layer that owns the decision:

| Decision | Owner |
| --- | --- |
| Which active session should receive this event? | `SessionRouter` |
| Is this a refinement, patch, rebuild, or new build? | Control plane |
| Which workflow sequence should execute? | `extension_registry.json` + control-plane route |
| Which agent speaks next inside one workflow? | `handoffs.yaml` compiled to AG2 `TransitionGraph` |
| Which UI state should the websocket show? | Runtime transport + `ui_config.yaml` |

Do not encode product-level route decisions in workflow-local handoffs. A
workflow-local transition can read deterministic context variables, tool results,
or typed structured-output state. Natural-language intent classification belongs
in the control plane before the workflow run is started or resumed.

## AG2 Beta Mapping

Mozaiks keeps workflow YAML as its authoring contract and compiles it to AG2 beta
runtime objects:

| Mozaiks contract | AG2 beta runtime object |
| --- | --- |
| `agents.yaml` | `Agent` registration |
| `handoffs.yaml` | `TransitionGraph` |
| `context_variables.yaml` | workflow context variables |
| `tools.yaml` | agent tools and typed routing outputs |
| runtime events | AG2 stream events normalized into Mozaiks domain events |

This keeps generator output deterministic while allowing the AG2 execution
backend to evolve underneath `OrchestrationPort`.
