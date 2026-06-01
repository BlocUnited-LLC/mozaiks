# Handoff Condition Scope Guide

## Problem

A handoff can drift when the workflow expects an approval state variable that no
longer exists on the live UI path. This causes missed transitions after the user
reviews an artifact and replies in chat.

## Canonical Approach

Use `handoffs.yaml` with deterministic context conditions. Composer replies that
need natural-language interpretation are classified by the control plane first;
the resulting route or approval state is then written into context and consumed
by the workflow transition graph.

## Example (`handoffs.yaml`)

```yaml
handoff_rules:
  - source_agent: user
    target_agent: ContextVariablesAgent
    handoff_type: condition
    condition_type: expression
    condition: ${workflow_review_approved} == true
    transition_target: AgentTarget

  - source_agent: user
    target_agent: PatternAgent
    handoff_type: condition
    condition_type: expression
    condition: ${workflow_review_revision_requested} == true
    transition_target: AgentTarget
```

## Notes

- Keep this in YAML declaratives (`handoffs.yaml`, `context_variables.yaml`,
  `tools.yaml`).
- Use composer replies for plain-text approval/feedback only when the control
  plane or a response-required tool writes the resulting context variable.
- Workflow-local handoffs compile to AG2 beta Network `TransitionGraph`; they do
  not run LLM classification during transition evaluation.
