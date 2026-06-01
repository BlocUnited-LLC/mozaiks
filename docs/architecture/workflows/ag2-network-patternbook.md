---
title: AG2 Network Patternbook
status: Authoritative - Pre-Production
created: 2026-05-31
updated: 2026-05-31
depends_on: declarative-ag2-mapping.md, workflow-authoring-contracts.md
---

# AG2 Network Patternbook

The AG2 Network patternbook is the canonical generation catalog for workflow
shape selection.

Canonical file:

```text
factory_app/workflows/AgentGenerator/patternbook/ag2_network_patterns.yaml
```

It plays the same role for workflows that `domain_catalogs.yaml` plays for app
modules: curated generation intelligence, not runtime authority.

## Ownership

| Layer | Owns |
| --- | --- |
| Patternbook YAML | Pattern taxonomy, intent signals, required context/tools, handoff-generation strategy |
| AgentGenerator | Reads the patternbook to select patterns and generate workflow YAML |
| Runtime compiler | Validates workflow YAML and compiles handoffs into AG2 beta `TransitionGraph` objects |
| Control plane | Classifies user/refinement intent before workflow launch or resume |

The runtime does not route from the patternbook directly. It routes from the
generated workflow contract.

## Rules

- Workflow YAML remains the source of truth.
- `handoffs.yaml` conditions are deterministic only.
- `condition_type: llm` and `condition_type: string_llm` are invalid.
- LLM classification may set context variables or structured output before
  routing.
- `context_variables.yaml` declares the state keys used by routing conditions.
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

`Coordinator` is the AG2 beta replacement for open-ended manager/specialist
selection. It should be generated only when the target host supports the full
WorkflowAdapter typed handoff path. Otherwise AgentGenerator should prefer
Context-Aware Routing or Star.

## AgentGenerator Usage

`PatternAgent` receives the full patternbook summary through
`inject_pattern_selection_guidance`.

`WorkflowBundleBuilderAgent` (the task batch worker that generates each full
workflow bundle) receives pattern-specific guidance through
`inject_workflow_bundle_builder_guidance` via `update_agent_state_pattern.py`,
including:

- graph strategy
- routing idiom
- required context variables
- required tools
- handoff-generation ordering
- terminal rule
- compact `WorkflowStrategy` examples

This gives each parallel builder worker a focused, pattern-specific briefing and
keeps the patternbook as the single catalog to evolve as AG2 beta Network support
expands.

## Boundary

The patternbook is not:

- workflow sequence routing
- transition UI routing
- refinement routing
- app navigation
- persistence authority

Those remain owned by their existing layers.
