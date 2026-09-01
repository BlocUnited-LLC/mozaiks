---
title: AG2 Network Patternbook
status: Authoritative - Pre-Production
created: 2026-05-31
updated: 2026-06-11
depends_on: declarative-ag2-mapping.md, workflow-authoring-contracts.md
---

# AG2 Network Patternbook

The AG2 Network patternbook is the canonical generation catalog for workflow
shape selection.

Canonical file:

```text
factory_app/build_context/AgentGenerator/ag2_network_patterns.yaml
```

It plays the same role for workflows that `domain_catalogs.yaml` plays for app
modules: curated generation intelligence, not runtime authority.

## Ownership

| Layer | Owns |
| --- | --- |
| Patternbook YAML | Pattern taxonomy, intent signals, required context/tools, handoff-generation strategy |
| AgentGenerator | Reads the patternbook to select patterns and generate workflow YAML |
| Runtime compiler | Validates workflow YAML, compiles handoffs into AG2 1.0 `TransitionGraph` objects, and resolves turns through `WorkflowAdapter` |
| Refinement Engine | Classifies user/refinement intent before workflow launch or resume |

The runtime does not route from the patternbook directly. It routes from the
generated workflow contract.

## Rules

- Workflow YAML remains the source of truth.
- `transition_graph.yaml` conditions are deterministic only.
- `condition_type` is `context_equals` for AG2 context-state routing,
  `context_expression` for Mozaiks expression routing over declared state, or
  `tool_called` for AG2 routing-tool packets.
- LLM classification belongs before routing: set context variables or structured
  output before the workflow routes, not inside transition conditions.
- `context_variables.yaml` declares every state key used by `condition_key` or
  `${...}` references in `context_expression`.
- `tools.yaml` declares tools that set state or, where supported, return typed
  AG2 routing objects.

## Pattern Support

The patternbook tracks both AG2 cookbook shape and Mozaiks support level.

| Pattern | Support | Runtime Shape |
| --- | --- | --- |
| Context-Aware Routing | `compiled_now` | Context variable branch graph |
| Escalation | `compiled_now` | Confidence/status branch graph |
| Feedback Loop | `compiled_now` | Bounded approval/revision loop |
| Hierarchical | `deterministic_subflow_now` | Manager-owned staged delegation |
| Coordinator | `workflow_adapter_required` | Typed `handoff` / `finish` tools |
| Pipeline | `compiled_now` | Fixed sequence graph |
| Redundant | `deterministic_sequence_now` | Sequential candidate collection plus evaluator |
| Star | `compiled_now` | Hub-spoke return graph |
| Triage with Tasks | `compiled_now` | Triage plan followed by typed task sequence |

`Coordinator` is the AG2 1.0 replacement for open-ended manager/specialist
selection. It should be generated only when the target host supports the full
WorkflowAdapter typed handoff path. Otherwise AgentGenerator should prefer
Context-Aware Routing or Star.

## AgentGenerator Usage

`factory_app/build_context/AgentGenerator/context.yaml` declares how the
patternbook is projected into AgentGenerator prompts.

`PatternAgent` receives the full patternbook summary through the
`ag2_network_patterns.yaml` asset projection with `render: summary`.

`WorkflowBundleBuilderAgent` (the task batch worker that generates each full
workflow bundle) receives only the selected pattern record through the same
catalog asset's `render: selected_record` projection with
`selected_by: current_task.pattern_id`, including:

- graph strategy
- routing idiom
- required context variables
- required tools
- handoff-generation ordering
- terminal rule
- compact `WorkflowStrategy` examples

The runtime prompt middleware is generic:

```text
mozaiksai.core.workflow.context.projection.inject_build_context_projections
```

Workflow-specific Python prompt stubs do not own patternbook projection. This
keeps each parallel builder worker focused, keeps the patternbook as the single
catalog to evolve as AG2 1.0 Network support expands, and lets other workflows
use the same context-declared projection contract.

## Boundary

The patternbook is not:

- workflow sequence routing
- transition UI routing
- refinement routing
- app navigation
- persistence authority

Those remain owned by their existing layers.


