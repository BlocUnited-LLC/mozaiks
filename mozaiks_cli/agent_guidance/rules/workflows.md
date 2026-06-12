---
paths:
  - "workflows/**"
---

# Workflow Rules

Workflows are app-local AI behavior.

Canonical workflow shape:

```text
workflows/{WorkflowName}/
  orchestrator.yaml
  agents.yaml
  transition_graph.yaml
  context_variables.yaml
  structured_outputs.yaml
  tools.yaml
  middleware.yaml
  ui_config.yaml
  tools/
  ui/
```

## Rules

- Keep workflow configuration declarative and structured-output-first.
- Put reasoning in agent prompts and structured outputs.
- Keep tools deterministic: persist, validate, emit events, or call declared APIs.
- Do not put classification/inference heuristics in tools.
- Use declared triggers and handoffs instead of hardcoded runtime assumptions.
- Keep workflow-specific UI under the workflow `ui/` folder only when the workflow needs an artifact surface.

