# AG2 Ownership Boundary

Mozaiks uses AG2 as the long-term agentic execution backbone. Mozaiks should
not grow a parallel agent framework for primitives AG2 already owns or is the
right upstream home for.

For the concrete runtime audit and shrink plan, see
[AG2 Execution Alignment Plan](ag2-execution-alignment-plan.md).
For the living AG2 upgrade checklist and divergence log, see
[AG2 Update Watchpoints](ag2-update-watchpoints.md).

## Boundary

AG2 owns generic agentic mechanics:

- agent primitives, model calls, tools, middleware, and task execution
- multi-agent network behavior, Hub, AgentClient, channels, adapters, and
  workflow state progression
- task delegation, task lifecycle observation, event mirroring, and generic
  runtime observability for agents

Mozaiks owns deterministic product and runtime contracts around AG2:

- declarative workflow files and strict structured-output validation
- canonical generated app, workflow, module, page, persistence, and secret
  artifact shapes
- app/runtime persistence, transport integration, tenant/session boundaries,
  Studio/platform lifecycle, and artifact promotion
- factory control-plane policy and deterministic decomposition contracts that
  define the typed work AG2 agents execute

## Runtime Handoff

AG2 network packets are the source trace for agent execution, not automatically
user-visible chat copy. Mozaiks owns the projection from AG2 packet history into
transport events and replayed chat history because that projection enforces
workflow contracts such as `ui_hidden` control signals and structured-output
artifact rendering.

Control tokens such as `NEXT` may drive declarative transition/context state, but
must not render as normal assistant messages when the matching trigger is marked
`ui_hidden`. Structured-output JSON from registered structured-output agents
must remain durable trace data and feed the declared artifact/tool path; it must
not be projected as raw chat text. User-visible agent output should be narrative
copy, Markdown, or an explicit UI artifact.

## Design Rule

When a workflow feature needs agentic behavior, first ask whether AG2 already
owns it. Prefer compiling Mozaiks declarations into AG2-native objects over
adding Mozaiks-owned orchestration loops.

Custom Mozaiks logic is appropriate when it enforces Mozaiks-specific contracts,
such as canonical artifact shapes, structured-output validation, app workspace
boundaries, tenant/session persistence, or deterministic decomposition
schemas. Custom logic is not appropriate when it is a generic replacement for
AG2 Hub, AgentClient, network adapters, task streams, task observation,
delegation engines, or agent scheduling.

## Missing AG2 Capability Process

If AG2 does not provide a required capability:

1. Inspect AG2's current docs, APIs, and source shape before adding runtime
   code.
2. Implement the smallest Mozaiks-owned layer that fits inside AG2's framework,
   preferably behind `mozaiksai.core.adapters` or another narrow boundary.
3. Document the intentional divergence in the relevant architecture doc and
   keep it visible as an AG2 compatibility watchpoint.
4. Prefer raising an upstream AG2 issue or proposal when the missing capability
   is generic agent orchestration rather than Mozaiks-specific contract
   enforcement.

## Task Decomposition

Mozaiks decomposition should stay contract-first:

```text
user intent
  -> DecompositionAgent structured output
  -> validated typed task graph
  -> AG2 agent/network execution
  -> validated canonical artifacts
```

Mozaiks may own the typed task graph when the graph represents canonical build
work. AG2 should own as much of the actual execution, lifecycle observation,
and agent communication as its APIs allow.

## Control Plane

Do not model the Mozaiks control plane as one free-running AG2 agent that
launches workflows by tool call. The control plane is deterministic
artifact-aware policy over generated app/workflow state: it classifies change
scope, checks artifact lineage, chooses checkpoints, validates route decisions,
and starts the selected workflow or scoped worker only after typed policy has
accepted the decision.

AG2 should still execute the LLM portions of that pipeline. Control-plane
checkpoints that need a model should use AG2 agent primitives through a narrow
adapter, currently `mozaiksai.core.adapters.AG2StructuredAgentRunner`, and
return strict structured outputs. Mozaiks then validates those outputs and
performs routing, promotion, invalidation, staged execution, and workflow
launching deterministically.

This keeps AG2 responsible for agent execution mechanics while keeping Mozaiks
responsible for app-specific artifact and lifecycle policy.

## Review Checklist

Before adding or changing workflow runtime code, confirm:

- The change does not replace an AG2 primitive that can be used directly.
- Any custom scheduler, adapter, or execution wrapper is Mozaiks-specific and
  documented.
- The implementation keeps AG2 imports behind existing adapter/runtime
  boundaries.
- Structured outputs and YAML contracts remain the source of truth for
  generated artifact shapes.
- AG2 upgrade watchpoints are documented wherever Mozaiks temporarily fills a
  missing AG2 capability.
