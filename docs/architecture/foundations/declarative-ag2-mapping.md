# Declarative Config to AG2 Mapping

This document maps workflow declaratives to AG2-native execution.

It also makes clear which parts of the Mozaiks architecture do not map to AG2
because they belong to the substrate or automation boundary.

## Core Point

Only workflow declaratives map to AG2.

These do not map to AG2:

- app substrate declaratives
- shell declaratives
- domain event contracts
- automation routes

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

### `agents.yaml`

Each agent entry becomes an AG2 agent definition after prompt composition and
tool binding.

### `handoffs.yaml`

Maps to AG2 handoff conditions and targets.

### `context_variables.yaml`

Maps to the shared workflow state container used during execution.

### `tools.yaml`

Maps to callable tool registration, with optional Mozaiks validation or wrapper
behavior.

### `hooks.yaml`

Maps to AG2 hook registration and workflow lifecycle integration.

## Mozaiks-Only Workflow Layers

### `structured_outputs.yaml`

Used for:

- typed validation
- deterministic auto-tool flows
- stronger execution guarantees than prompt text alone

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
- `platform/automations/*`
- `platform/shell/*`

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

## Cross References

- [workflow-architecture.md](workflow-architecture.md)
- [event-system-architecture.md](event-system-architecture.md)
- [core-product-app-bundle-boundary.md](core-product-app-bundle-boundary.md)
