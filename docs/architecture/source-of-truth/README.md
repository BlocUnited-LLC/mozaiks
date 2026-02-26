# Source of Truth - Architecture Documents

These documents are the authoritative architectural references for mozaiks. When other docs conflict with these, these win.

## The Documents

| # | Document | What It Answers |
|---|---|---|
| 1 | [UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md](UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md) | How `ask/workflow/view` surfaces work and which state invariants must hold |
| 2 | [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md) | Runtime responsibilities, workflow inputs, AG-UI boundary, and execution modes |
| 3 | [PROCESS_AND_EVENT_MAP.md](PROCESS_AND_EVENT_MAP.md) | Runtime processes, transports, event categories, and mode-to-process mapping |
| 4 | [LEARNING_LOOP_ARCHITECTURE.md](LEARNING_LOOP_ARCHITECTURE.md) | Telemetry contracts and quality-improvement feedback loop boundaries |
| 5 | [EVENT_TAXONOMY.md](EVENT_TAXONOMY.md) | Canonical event families, naming rules, and envelope semantics |
| 6 | [EVENT_SYSTEM_ARCHITECTURE.md](EVENT_SYSTEM_ARCHITECTURE.md) | Event channels, dispatch components, and runtime event-flow rules |
| 7 | [GRAPH_INJECTION_CONTRACT.md](GRAPH_INJECTION_CONTRACT.md) | Graph injection/mutation YAML contract and runtime behavior |
| 8 | [APP_CREATION_GUIDE.md](APP_CREATION_GUIDE.md) | Practical OSS workflow/app build path on top of mozaiks |

## Key Concept: Three Execution Modes

Execution modes are invocation flows. They are separate from runtime responsibilities (`core`, `orchestration`) and workflow inputs.

- Mode 1: AI Workflow
- Mode 2: Triggered Action
- Mode 3: Plain App

Artifacts bridge the modes.

## Reading Order

1. `UI_SURFACE_AND_LAYOUT_ARCHITECTURE`
2. `WORKFLOW_ARCHITECTURE`
3. `EVENT_TAXONOMY`
4. `EVENT_SYSTEM_ARCHITECTURE`
5. `PROCESS_AND_EVENT_MAP`
6. `GRAPH_INJECTION_CONTRACT`
7. `LEARNING_LOOP_ARCHITECTURE`
8. `APP_CREATION_GUIDE`

## Relationship Map

```text
UI_SURFACE_AND_LAYOUT_ARCHITECTURE
             |
             v
WORKFLOW_ARCHITECTURE <-------> PROCESS_AND_EVENT_MAP
         |                              |
         v                              v
   EVENT_TAXONOMY <------> EVENT_SYSTEM_ARCHITECTURE
         |                              |
         v                              v
GRAPH_INJECTION_CONTRACT      LEARNING_LOOP_ARCHITECTURE
```
