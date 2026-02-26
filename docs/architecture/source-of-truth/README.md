# Source of Truth - Architecture Documents

These documents are the authoritative architectural references for mozaiks. When other docs conflict with these, **these win**.

## The Documents

| # | Document | What It Answers |
|---|----------|----------------|
| 1 | [UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md](UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md) | How do `ask/workflow/view` surfaces work? What are the canonical `layoutMode` rules, transitions, and mobile behavior? |
| 2 | [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md) | What are workflows? How are they structured? What are the three execution modes? How does capability decomposition work? How do `core` and `orchestration` runtime responsibilities relate to workflow inputs? |
| 3 | [PROCESS_AND_EVENT_MAP.md](PROCESS_AND_EVENT_MAP.md) | What processes run? What transports connect them? Where does each event go? How do the three modes use different processes? |
| 4 | [LEARNING_LOOP_ARCHITECTURE.md](LEARNING_LOOP_ARCHITECTURE.md) | How do workflows improve over time? What telemetry is needed? What are the recursive optimization loops? |
| 5 | [EVENT_TAXONOMY.md](EVENT_TAXONOMY.md) | What are the canonical domain event types? What does the event envelope look like? |
| 6 | [EVENT_SYSTEM_ARCHITECTURE.md](EVENT_SYSTEM_ARCHITECTURE.md) | What is the target event dispatch layer? ChatDispatcher, BusinessDispatcher, Metering? |
| 7 | [GRAPH_INJECTION_CONTRACT.md](GRAPH_INJECTION_CONTRACT.md) | How does FalkorDB graph injection work? What are injection and mutation rules? |
| 8 | [APP_CREATION_GUIDE.md](APP_CREATION_GUIDE.md) | How do I build an app on mozaiks? (OSS developer walkthrough) |

## Key Concept: Three Execution Modes

Execution modes are invocation flows. They are separate from runtime responsibilities (`core`, `orchestration`) and workflow inputs.

- **Mode 1: AI Workflow** - Chat -> agent -> artifact (full orchestration runtime, WebSocket, AG-UI)
- **Mode 2: Triggered Action** - Button click -> mini-run or direct function (may or may not use AI)
- **Mode 3: Plain App** - Pages, CRUD, settings, dashboards (no AI, reads persisted artifacts)

**Artifacts bridge the modes.** AI creates them (Mode 1), buttons update them (Mode 2), pages display them (Mode 3). See WORKFLOW_ARCHITECTURE.md section "Three Execution Modes" and PROCESS_AND_EVENT_MAP.md section "Three Execution Modes and How They Map to Processes."

## Reading Order

If you're starting from zero:

1. **UI_SURFACE_AND_LAYOUT_ARCHITECTURE** - understand UI surface semantics (`ask/workflow/view`) and responsive layout behavior
2. **WORKFLOW_ARCHITECTURE** - understand what a workflow is
3. **EVENT_TAXONOMY** - understand the domain event model
4. **EVENT_SYSTEM_ARCHITECTURE** - understand how events are dispatched
5. **PROCESS_AND_EVENT_MAP** - understand the full runtime picture (processes + transports + traces)
6. **GRAPH_INJECTION_CONTRACT** - understand agent memory via FalkorDB
7. **LEARNING_LOOP_ARCHITECTURE** - understand how quality improves over time

## Relationship Map

```
UI_SURFACE_AND_LAYOUT_ARCHITECTURE
   (ask/workflow/view semantics)
             |
             v
WORKFLOW_ARCHITECTURE <-------> PROCESS_AND_EVENT_MAP
    (what things are)              (where things run)
         |                              |
         |                              |
         v                              v
   EVENT_TAXONOMY <------> EVENT_SYSTEM_ARCHITECTURE
   (domain model)           (dispatch implementation)
         |                              |
         |                              |
         v                              v
 GRAPH_INJECTION_CONTRACT      LEARNING_LOOP_ARCHITECTURE
   (agent memory)               (quality feedback loop)
```
