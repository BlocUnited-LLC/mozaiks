# Handoff Condition Scope Guide

## Problem

A handoff can evaluate before UI-driven context updates are available if you use
the wrong scope. This causes missed transitions (for example after a user clicks
approve in a UI tool).

## Canonical Approach

Use `handoffs.yaml` with `handoff_type: condition` and set
`condition_scope: pre` for context-based checks that should run before reply.

## Example (`handoffs.yaml`)

```yaml
handoff_rules:
  - source_agent: user
    target_agent: ContextVariablesAgent
    handoff_type: condition
    condition_type: expression
    condition: ${action_plan_acceptance} == "accepted"
    condition_scope: pre
    transition_target: AgentTarget

  - source_agent: user
    target_agent: ActionPlanArchitect
    handoff_type: condition
    condition_type: llm
    condition: "When the user requests changes to the action plan."
    transition_target: AgentTarget
```

## Example (`context_variables.yaml`)

```yaml
definitions:
  action_plan_acceptance:
    type: string
    description: Tracks whether the user accepted the action plan
    source:
      type: state
      default: pending
      triggers:
        - type: ui_response
          tool: action_plan
          response_key: plan_acceptance

agents:
  ContextVariablesAgent:
    variables:
      - action_plan_acceptance
```

## Notes

- Keep this in YAML declaratives (`handoffs.yaml`, `context_variables.yaml`,
  `tools.yaml`).
- Use `condition_scope: pre` for UI-driven acceptance/rejection gates.
