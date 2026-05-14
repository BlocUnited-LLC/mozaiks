# Add Workflows

Most users should start in the Console with `Create App`. Add a workflow
directly only when you are extending Mozaiks or adding an app-owned workflow.

## Workflow Authoring Model

A Mozaiks workflow is a deterministic state machine around AG2 agents.

Before writing files, define:

- the outcome the workflow must produce
- the agents needed to interview, reason, and generate structured output
- the data contracts that must be persisted or shown to the user
- the UI artifacts, if any, that should appear in the chat stream
- whether the workflow is standalone or triggered mid-flight by a parent workflow

## Choose The Owner

Use `factory_app/workflows/{WorkflowName}/` for shared builder workflows such as
app generation, workflow generation, and refinement journeys.

Use `app/workflows/{WorkflowName}/` for workflows that belong to one generated
app workspace.

## Canonical File Set

```text
workflows/{WorkflowName}/
├── orchestrator.yaml
├── context_variables.yaml
├── agents.yaml
├── structured_outputs.yaml
├── tools.yaml
├── handoffs.yaml
├── ui_config.yaml
├── hooks.yaml
├── extended_orchestration/
│   └── mfj_extension.json
├── tools/
│   ├── __init__.py
│   └── artifact_tools.py
└── ui/{WorkflowName}/
    └── components/
```

`hooks.yaml`, `extended_orchestration/`, workflow tools, and workflow UI are
included only when the workflow needs them.

## Authoring Order

1. Define `orchestrator.yaml`: entry agent, startup mode, turn limits, and human review.
2. Define `context_variables.yaml`: shared state and parent-injected values.
3. Define `structured_outputs.yaml`: strict output models for generator agents.
4. Define `agents.yaml`: conversational agents gather context; generator agents emit typed output.
5. Define `tools.yaml`: bind dumb tools and optional UI emission.
6. Define `handoffs.yaml`: deterministic routing between agents and the user.
7. Define `ui_config.yaml`: list visual agents that should stream to the UI.

## Tool Rule

Tools stay dumb. Agents reason through prompts and structured outputs. Tool code
should read structured output, persist or transform it deterministically, and
emit UI events when needed.

## UI Rule

Use workflow UI components only for artifacts that belong in the chat stream.
Persistent app pages belong under the generated app workspace, not inside a
workflow UI folder.

## Read Next

- [Workflow Architecture](../../architecture/workflows/workflow-architecture.md)
- [Workflow Authoring Contracts](../../architecture/workflows/workflow-authoring-contracts.md)
- [Mid-Flight Journeys](../../architecture/mozaiksai/mid-flight-journeys.md)
