# Declarative Config to AG2 Mapping

This document maps canonical workflow YAML declaratives to AG2-native execution.

Runtime loading is strict. Files are validated by typed contracts (Pydantic with
`extra="forbid"`), so examples here use only canonical shapes.

## Core Point

Only workflow declaratives map to AG2.

These do not map to AG2:

- app backend declaratives
- shell declaratives
- domain event contracts
- workflow triggers

Those are Mozaiks layers that exist before a workflow starts.

## Workflow File Mapping

| Workflow file | Role | AG2 relationship |
| --- | --- | --- |
| `orchestrator.yaml` | pattern, turns, startup behavior | mostly AG2-native |
| `agents.yaml` | agent roster and prompts | AG2-native with Mozaiks composition helpers |
| `handoffs.yaml` | routing rules inside the workflow | AG2-native |
| `context_variables.yaml` | workflow state bindings | AG2-native container plus Mozaiks adapters |
| `tools.yaml` | tool declarations | AG2 tool calling plus Mozaiks wrappers |
| `hooks.yaml` | lifecycle and hook registration | AG2-native hooks plus Mozaiks convenience |
| `structured_outputs.yaml` | typed runtime validation | Mozaiks layer |
| `ui_config.yaml` | frontend exposure metadata | frontend-only |
| `_pack/workflow_graph.json` | MFJ or journey graph input | Mozaiks orchestration layer |

## Native or Near-Native Mappings

### `orchestrator.yaml`

Maps to workflow-local execution concerns such as:

- pattern selection
- startup mode
- initial agent
- initial message
- turn budget

Canonical startup key is `workflow_startup_mode` (`AgentDriven`, `UserDriven`,
`BackendOnly`).

### `agents.yaml`

Each agent entry becomes an AG2 agent definition after prompt composition and
tool binding.

### `handoffs.yaml`

Maps to AG2 handoff conditions and targets.

### `context_variables.yaml`

Maps to the shared workflow state container used during execution.

Canonical shape:

```yaml
definitions:
  var_name:
    type: string
    source:
      type: state
      default: null
agents:
  AgentName:
    variables:
      - var_name
```

### `tools.yaml`

Maps to callable tool registration, with optional Mozaiks validation or wrapper
behavior.

Canonical shape:

```yaml
tools:
  - agent: AgentName
    file: tool_file.py
    function: run_tool
    tool_type: Agent_Tool
lifecycle_tools: []
```

### `hooks.yaml`

Maps to AG2 hook registration and workflow lifecycle integration.

Canonical shape:

```yaml
hooks:
  - hook_type: update_agent_state
    hook_agent: AgentName
    filename: hook_file.py
    function: update_state
```

## Mozaiks-Only Workflow Layers

### `structured_outputs.yaml`

Used for:

- typed validation
- deterministic auto-tool flows
- stronger execution guarantees than prompt text alone

Canonical shape:

```yaml
registry:
  AgentName: ModelName
models:
  ModelName:
    type: model
    fields:
      field_name:
        type: str
```

### `ui_config.yaml`

Used for:

- frontend rendering metadata
- agent visibility rules

### `_pack/workflow_graph.json`

Used for:

- MFJ structure
- child workflow coordination
- journey-level graph execution

These are workflow-runtime features around AG2, not AG2-native concepts.

## What Sits Before AG2

The following app-bundle families are consumed before AG2 is involved:

- `platform/data/*`
- `platform/modules/*`
- `platform/shell/*`
- workflow `triggers:` declared in `platform/workflows/*/orchestrator.yaml`

Most importantly:

- a domain event becomes a route decision before AG2 sees a workflow input

That route decision belongs to Mozaiks.

## Design Guardrails

Do not use AG2 as the conceptual home for:

- CRUD state
- navigation
- settings and subscription policy
- domain event naming
- event-to-workflow policy

Use AG2 for what it is good at:

- conversations
- handoffs
- tool use
- agent coordination inside a workflow

## Authoring Note

Use `*.yaml` declaratives in workflow bundles.

## Cross References

- [workflow-architecture.md](workflow-architecture.md)
- [event-system-architecture.md](event-system-architecture.md)
- [core-product-app-bundle-boundary.md](core-product-app-bundle-boundary.md)
