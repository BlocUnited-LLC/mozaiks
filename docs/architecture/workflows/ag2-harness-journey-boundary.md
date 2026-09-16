# AG2 Harness, Mozaiks Journeys, and ACP

This document defines the ownership boundary between AG2 execution primitives,
Mozaiks workflow journeys, and the refinement control plane.

## The short version

Mozaiks uses AG2 to execute agents. Mozaiks owns the product journey around
those executions.

```text
user objective
  -> Mozaiks journey/session router
  -> workflow sequence
  -> AG2 Network execution for one workflow
  -> checkpoint, artifact, validation, or human input
  -> next workflow or refinement decision
```

AG2's [Agent Harness](https://docs.ag2.ai/docs/user-guide/agent_harness/)
belongs inside an agent execution. It assembles context, manages agent
knowledge and bounded subtasks, and coordinates agent-local middleware and
observers. It does not own Mozaiks journey identity, workflow sequence
selection, artifact lineage, or promotion.

AG2 [middleware](https://docs.ag2.ai/docs/user-guide/middleware/) is also
agent-turn scoped. It is appropriate for telemetry, retries, request
mutation, tool auditing, and guardrails. It must not become a second workflow
router or a hidden refinement policy engine.

## Ownership by layer

| Layer | Owner | Responsibility |
| --- | --- | --- |
| Agent context and memory | AG2 Agent Harness | Assembly, knowledge, bounded agent subtasks, observers, HITL |
| Per-agent cross-cutting behavior | AG2 Middleware | Logging, retries, guardrails, tool and human-input hooks |
| One workflow's agent graph | AG2 Network plus Mozaiks workflow contracts | Agent execution, local transitions, structured outputs, tool calls |
| Cross-workflow journey | `mozaiksai.core.session.router` | `journey_id`, sequence selection, checkpoints, resume, and handoff |
| Refinement lifecycle | `mozaiksai.control_plane` | Classification, route selection, scope, staging, validation, and promotion policy |
| Refinement declarations | `factory_app/refinement_harness` | Checkpoint, route, prompt, and tool declarations consumed by the control plane |
| External coding agent | ACP adapter | Controlled provider session for coding work after scope and permissions are established |

The refinement harness is therefore a Mozaiks control-plane pack, not a second
AG2 Agent Harness. The similar names describe different scopes.

## ACP placement

In AG2's [ACP client model](https://docs.ag2.ai/docs/user-guide/acp/client/),
AG2 drives an external CLI coding agent through ACP and receives its streamed
events. Mozaiks may use that capability behind the coding-provider boundary in
the refinement lane. Before an ACP session starts, Mozaiks must already have a
scoped artifact workspace, a permitted change route, and a staging target.

ACP must not select the workflow sequence, mutate the canonical artifact store
directly, or promote its own output. Its result is evidence for the same
validation and promotion path used by other coding providers.

## Context authority

Build contexts, `AppContext`, artifact revisions, and journey records remain
Mozaiks authorities. An AG2 `KnowledgeStore` can provide agent-local memory,
but copying those canonical records into it would create a second source of
truth. The runtime should inject the required scoped context through the
existing Mozaiks context and tool boundaries.

AG2 task delegation is suitable for bounded work inside one workflow agent.
It must not be used to create an untracked replacement for the Mozaiks
journey router or to advance workflows outside the declared sequence.

## Production acceptance gate

The one-workflow smoke helper is a diagnostic. It is not evidence that a build
journey completed. The production acceptance path must use the normal journey
entrypoint and verify all of the following:

1. one immutable `journey_id` is created and carried by every child workflow;
2. each child has its own `workflow_run_id` and remains associated with the
   pinned workflow sequence;
3. a human-input checkpoint can pause and resume the same journey;
4. terminal execution and settled accounting are distinguished;
5. evaluation reads settled evidence without selecting or rewriting route
   policy;
6. refinement output is staged and validated before promotion;
7. a failed or cancelled child cannot advance the journey or promote an
   artifact.

The implementation contract for these guarantees is ADR 0006,
`provider-neutral-bounded-multi-workflow-journey-execution`. This document
clarifies how AG2 fits into that contract; it does not introduce a new
orchestration path.

## Decisions

- Keep Mozaiks journey routing above AG2 workflow execution.
- Use AG2 Harness and Middleware within their documented agent-turn scope.
- Use ACP as a provider boundary for controlled coding sessions.
- Keep refinement classification, artifact scope, staging, validation, and
  promotion in the Mozaiks control plane.
- Do not duplicate canonical build or artifact state in AG2 knowledge.
- Do not add a dedicated AG2-based `RefinementWorkflow` unless the product
  contract changes explicitly and the ownership boundary is revised first.
