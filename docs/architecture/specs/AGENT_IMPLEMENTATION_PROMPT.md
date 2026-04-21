# Mozaiks Implementation Prompt for Coding Agents

**Purpose:** This document provides the context and instructions for coding agents to implement the Mozaiks architecture.

---

## Your Mission

You are implementing **Mozaiks**, a modular AI-powered application platform. The architecture separates concerns into independent packages that communicate through events, not direct imports.

---

## CRITICAL: Read These Documents First

Before writing any code, you MUST read and understand:

1. **⭐⭐ EVENT_DRIVEN_EXECUTION_SPEC.md** - The runtime is event-first. All orchestration reacts to explicit events, NOT transcript parsing or output discovery.

2. **MODULAR_ARCHITECTURE_V2.md** - Package structure and dependency rules.

3. **AGENTS_MD_V2.md** - Import rules and communication patterns.

4. **EVENT_CONTRACTS.md** - Event envelope schema and naming conventions.

5. **RUNTIME_SPEC.md** - How the runtime composes packages.

If the task touches app generation, builder workflows, persistent app UI, or refinement routing, also read:

6. **agentic-app-generation-strategy.md** - Canonical artifact model for turning intent into an agentic app.
7. **agentic-app-generation-checklist.md** - Phase-gated implementation plan that prevents workflow-first drift.

---

## Architecture Overview

```
                          ┌─────────┐
                          │   cli   │
                          └────┬────┘
                               │
                               ▼
                          ┌─────────┐
                          │ runtime │
                          └────┬────┘
                               │
                ┌──────────────┼──────────────┐
                │              │              │
                ▼              ▼              ▼
          ┌─────────┐    ┌─────────┐    ┌─────────┐
          │   ai    │    │   ui    │    │ modules │
          └────┬────┘    └────┬────┘    └────┬────┘
               │              │              │
               └──────────────┼──────────────┘
                              │
                              ▼
                         ┌─────────┐
                         │  core   │
                         └─────────┘
```

---

## Implementation Order

Follow this order to ensure dependencies are satisfied:

### Phase 1: Core Package (`packages/core/`)

**What to implement:**
- Event envelope class with JSON schema validation
- ExecutionRequest/ExecutionResult interfaces
- Storage abstractions (document store interface)
- Config loading utilities (YAML, env vars)
- JWT validation utilities
- Tenant context definition

**Key files to create:**
```
packages/core/
├── __init__.py
├── events.py          # Event class with create() method
├── interfaces.py      # Protocol classes (Executor, Storage, etc.)
├── config.py          # Config loading
├── context.py         # RequestContext, TenantContext
└── storage.py         # Storage abstractions
```

**Constraints:**
- NO imports from mozaiks_ai, mozaiks_modules, mozaiks_runtime, mozaiks_ui
- Only standard library, pydantic, pyyaml, motor

---

### Phase 2: AI Package (`packages/ai/`)

**What to implement:**
- Workflow YAML loader
- AG2 agent creation from workflow definitions
- Event adapter (AG2 events → normalized events)
- Tool registry and execution
- WebSocket streaming transport
- Chat persistence

**Key files to create:**
```
packages/ai/
├── __init__.py
├── adapter.py         # AG2 → normalized event adapter
├── workflows/
│   ├── loader.py      # Load workflow.yaml files
│   └── executor.py    # Run workflows
├── agents/
│   └── factory.py     # Create AG2 agents from config
├── tools/
│   ├── registry.py    # Tool registration
│   └── executor.py    # Tool execution
└── transport/
    └── websocket.py   # WebSocket streaming
```

**CRITICAL - Event Adapter Pattern:**
```python
# The adapter MUST iterate AG2 events in real-time and emit normalized events IMMEDIATELY
async def run_workflow(self, workflow_id: str, input_data: dict):
    workflow = self.load_workflow(workflow_id)

    async for ag2_event in workflow.run_iter():
        # Normalize and yield IMMEDIATELY - no batching
        normalized = self._normalize_event(ag2_event)
        yield normalized

        # If this is a decomposition event, emit runtime event
        if normalized.type == "process.decomposition_complete":
            yield Event.create(
                event_type="runtime.decomposition_planned",
                payload=normalized.payload
            )
```

**Constraints:**
- CAN import: mozaiks_core, ag2, openai, anthropic
- CANNOT import: mozaiks_modules, mozaiks_runtime, mozaiks_ui

---

### Phase 3: Modules Package (`packages/modules/`)

**What to implement:**
- Module definition loading
- Module executor
- Context injection for modules
- Event emission on CRUD operations

**Key files to create:**
```
packages/modules/
├── __init__.py
├── loader.py          # Load module definitions
├── executor.py        # Execute module actions
└── events.py          # Emit module events
```

**Constraints:**
- CAN import: mozaiks_core
- CANNOT import: mozaiks_ai, mozaiks_runtime, mozaiks_ui

---

### Phase 4: UI Package (`packages/ui/`)

**What to implement:**
- Page schema definitions
- Component registry
- Theme token resolution
- Server-side rendering support

**Constraints:**
- CAN import: mozaiks_core, jinja2
- CANNOT import: mozaiks_ai, mozaiks_modules, mozaiks_runtime

---

### Phase 5: Runtime Package (`packages/runtime/`)

**What to implement:**
- App definition loading (app.yaml)
- Request routing
- Executor registry (injecting AI/modules executors into context)
- Event bus with routing rules
- Mode detection (ai-only, modules-only, full)

**This is the ONLY package that can import from ai and modules.**

**Key files to create:**
```
packages/runtime/
├── __init__.py
├── app.py             # App loading and composition
├── router.py          # Request routing
├── executors.py       # Executor registry
├── events/
│   ├── bus.py         # Event bus
│   └── router.py      # Event routing rules
└── server.py          # Unified HTTP server
```

---

### Phase 6: CLI Package (`packages/cli/`)

**What to implement:**
- `mozaiks new` - Project scaffolding
- `mozaiks dev` - Development server
- `mozaiks build` - Production build
- `mozaiks add` - Add workflows/modules

---

## ABSOLUTE CONSTRAINTS

### 1. Event-First Orchestration

```
❌ WRONG: Parse transcript to discover structured outputs
❌ WRONG: Poll for completion states
❌ WRONG: Infer state from message content

✅ RIGHT: React to explicit events
✅ RIGHT: Emit events immediately when state changes
✅ RIGHT: Use event causation chains for tracing
```

### 2. Event Layer Separation

Events belong to ONE layer only:

| Layer | Examples | Purpose |
|-------|----------|---------|
| Domain | `contacts.created`, `orders.updated` | Business logic |
| Runtime Execution | `task.started`, `artifact.ready`, `runtime.decomposition_planned` | Orchestration |
| Control-plane | `app.patch_requested`, `app.design_change_requested`, `approval.required` | System control |

**NEVER mix layers in event handlers.**

### 3. No Direct Package Imports

```python
# ❌ FORBIDDEN in packages/ai/
from mozaiks_modules import ModuleExecutor

# ✅ CORRECT - use runtime-injected executor
module_executor = context.executors.get("modules")
```

### 4. Normalized Event Vocabulary

All events MUST use this vocabulary:

```
process.*      - Workflow/process lifecycle
task.*         - Individual task execution
artifact.*     - Generated content
chat.*         - User interaction
runtime.*      - Orchestration decisions
```

---

## Quick Reference: Event Types

### Process Events
- `process.started` - Workflow began
- `process.decomposition_complete` - Plan created
- `process.completed` - Workflow finished
- `process.failed` - Workflow failed

### Task Events
- `task.started` - Task began
- `task.progress` - Task progress update
- `task.completed` - Task finished
- `task.failed` - Task failed

### Artifact Events
- `artifact.draft_ready` - Draft created
- `artifact.updated` - Artifact modified
- `artifact.finalized` - Artifact complete

### Runtime Events
- `runtime.decomposition_planned` - MFJ trigger
- `runtime.build_requested` - Build trigger
- `runtime.revision_requested` - Revision trigger

### Control-Plane Events
- `app.patch_requested` - Localized change request
- `app.design_change_requested` - Design/schema re-entry request
- `app.feature_change_requested` - Scoped rebuild request
- `app.core_change_requested` - Upstream restart request

---

## Testing Your Implementation

Before committing, verify:

1. **No boundary violations:**
   ```bash
   python scripts/check_boundaries.py
   ```

2. **Events are normalized:**
   - All emitted events use the envelope schema
   - Event types follow the vocabulary

3. **No transcript parsing:**
   - Search for patterns like `"structured_output" in message`
   - These indicate architecture violations

4. **Immediate event emission:**
   - Events are yielded as they occur, not batched

---

## Getting Started Checklist

- [ ] Read EVENT_DRIVEN_EXECUTION_SPEC.md
- [ ] Read MODULAR_ARCHITECTURE_V2.md
- [ ] Read AGENTS_MD_V2.md
- [ ] Implement core package first
- [ ] Add Event class with proper envelope
- [ ] Implement AI adapter with real-time event iteration
- [ ] Verify no forbidden imports
- [ ] Test event emission flow

---

## Questions to Ask Yourself

Before writing code:

1. "Does this belong in core, ai, modules, runtime, or ui?"
2. "Am I importing a forbidden package?"
3. "Am I emitting events immediately or batching?"
4. "Am I parsing transcripts instead of reacting to events?"
5. "Does my event follow the naming convention?"

---

## Document Locations

All architecture documents are in `docs/architecture/specs/`:

- [INDEX.md](./INDEX.md) - Document index and quick reference
- [EVENT_DRIVEN_EXECUTION_SPEC.md](./EVENT_DRIVEN_EXECUTION_SPEC.md) - **START HERE**
- [RUNTIME_SPEC.md](./RUNTIME_SPEC.md) - Runtime behavior
- [WORKFLOW_TRIGGERS_SPEC.md](./WORKFLOW_TRIGGERS_SPEC.md) - Event triggers
- [TOOLS_SPEC.md](./TOOLS_SPEC.md) - Tool definitions
- [MODULAR_ARCHITECTURE_V2.md](./MODULAR_ARCHITECTURE_V2.md) - Package structure
- [AGENTS_MD_V2.md](./AGENTS_MD_V2.md) - Import rules
- [EVENT_CONTRACTS.md](./EVENT_CONTRACTS.md) - Event schemas
