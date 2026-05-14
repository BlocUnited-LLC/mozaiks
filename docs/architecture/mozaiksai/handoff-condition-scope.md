# Handoff Condition Scope Guide

## Problem

A handoff can drift when the workflow expects an approval state variable that no
longer exists on the live UI path. This causes missed transitions after the user
reviews an artifact and replies in chat.

## Canonical Approach

Use `handoffs.yaml` with `handoff_type: condition` and set
`condition_type: string_llm` when the user is replying through the composer after
reviewing a one-way artifact such as a diagram.

## Example (`handoffs.yaml`)

```yaml
handoff_rules:
  - source_agent: user
    target_agent: ContextVariablesAgent
    handoff_type: condition
    condition_type: string_llm
    condition: When the user approves the proposed workflow, confirms they want
      to proceed, says the sequence diagram looks correct, or indicates there are
      no further changes needed.
    transition_target: AgentTarget

  - source_agent: user
    target_agent: PatternAgent
    handoff_type: condition
    condition_type: string_llm
    condition: When the user requests changes, gives critique, or asks for revisions
      to the proposed workflow.
    transition_target: AgentTarget
```

## Notes

- Keep this in YAML declaratives (`handoffs.yaml`, `context_variables.yaml`,
  `tools.yaml`).
- Use composer replies for plain-text approval/feedback after one-way artifact review.
- Reserve context-expression gates for state that is actually produced by the live UI contract.
