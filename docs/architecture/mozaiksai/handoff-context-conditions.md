# Handoff Context Conditions

## Routing Model

Workflow handoffs are deterministic. All transition conditions in
`transition_graph.yaml` evaluate context variables at turn time — no LLM
classification happens inside routing.

LLM reasoning happens before routing: a Refinement Engine route, structured agent
output, or an agent tool writes the result into a context variable. The
transition graph then routes deterministically on that value.

## Canonical Approach

Declare state variables in `context_variables.yaml` and reference them in
`transition_graph.yaml` using `condition_type: context_equals` for simple
equality or `condition_type: context_expression` for composite deterministic
context checks.

```yaml
transition_rules:
  - source_agent: user
    target_agent: ContextVariablesAgent
    transition_type: condition
    condition_type: context_equals
    condition_key: workflow_review_approved
    condition_value: true
    transition_target: AgentTarget

  - source_agent: user
    target_agent: PatternAgent
    transition_type: condition
    condition_type: context_equals
    condition_key: workflow_review_revision_requested
    condition_value: true
    transition_target: AgentTarget
```

## Rules

- `condition_type` is `context_equals` for context-state equality,
  `context_expression` for Mozaiks checks over declared `${context_variable}`
  references, or `tool_called` for AG2 routing-tool packets.
- `transition_type` is `after_turn` (unconditional) or `condition`
  (AG2 condition-gated).
- Use `context_variables.yaml` to declare state keys; use `tools.yaml` to
  declare tools that write them.
- Keep `transition_graph.yaml`, `context_variables.yaml`, and `tools.yaml` as
  the three-file routing unit. Do not inline routing logic in agent prompts or
  tool implementations.
- Workflow-local handoffs compile to AG2 1.0 beta Network `TransitionGraph`; they
  do not run LLM classification during transition evaluation.
