# Event-Driven Execution Specification

**Status:** Specification (Critical Update)
**Created:** 2026-04-06
**Depends on:** RUNTIME_SPEC.md, WORKFLOW_TRIGGERS_SPEC.md
**Related:** mozaiks/docs/reference/deep-dives/ag2-beta-groupchat-strategy.md

This document defines the **event-first execution model** for Mozaiks. This is not a new system - it's a correction and alignment pass to ensure orchestration is driven by explicit events, not transcript parsing or implicit state inference.

---

## Core Principle

> **The runtime is event-first, not output-first.**

This means:
- Workflows and agents DO NOT drive orchestration through structured outputs alone
- Workflows MUST emit explicit runtime events at key checkpoints
- The runtime reacts to events, not inferred transcript state or post-hoc output inspection

> **Implementation:** See [RUNTIME_SPEC.md Section 6](./RUNTIME_SPEC.md#6-event-coordination) for the EventCoordinator implementation that handles event routing, trigger resolution, and platform forwarding.

---

## What We're Moving Away From

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ❌ OLD MODEL (WRONG)                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. Run workflow to completion                                               │
│  2. Read transcript or side effects afterward                                │
│  3. Parse structured outputs to discover what happened                       │
│  4. Infer workflow state from text patterns                                  │
│  5. Trigger next steps based on inference                                    │
│                                                                              │
│  PROBLEMS:                                                                   │
│  ─────────                                                                   │
│  • Fragile transcript parsing                                                │
│  • Implicit completion detection                                             │
│  • Loosely inferred workflow state                                           │
│  • Post-hoc orchestration decisions                                          │
│  • No real-time reaction capability                                          │
│  • Structured outputs treated as orchestration triggers                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## What We're Moving Toward

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ✅ NEW MODEL (CORRECT)                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. Workflow emits explicit checkpoint events                                │
│  2. Runtime adapter captures events in real-time                             │
│  3. Events normalized to stable runtime vocabulary                           │
│  4. Orchestration reacts to events immediately                               │
│  5. Decisions made during execution, not after                               │
│                                                                              │
│  PROPERTIES:                                                                 │
│  ───────────                                                                 │
│  • Explicit event emission                                                   │
│  • Normalized runtime event streams                                          │
│  • Deterministic orchestration                                               │
│  • Real-time reaction capability                                             │
│  • Events are source of truth (not transcript)                               │
│  • Structured outputs are inputs, not orchestration triggers                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Event Layer Separation (Mandatory)

Three distinct event layers must remain separate. They must NEVER be mixed.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        EVENT LAYER ARCHITECTURE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│  LAYER 1: DOMAIN EVENTS (Module Layer)                                       │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  Facts about the application domain.                                         │
│  Emitted by modules when data changes.                                       │
│                                                                              │
│  Examples:                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ contacts.created         # A contact was created                      │ │
│  │ orders.updated           # An order was modified                      │ │
│  │ invoices.paid            # An invoice payment completed               │ │
│  │ users.role_changed       # User permissions changed                   │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│  LAYER 2: RUNTIME EXECUTION EVENTS (Workflow Layer)                          │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  Live execution signals about workflow progress.                             │
│  Emitted by workflows/agents during execution.                               │
│                                                                              │
│  Examples:                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ task.started             # A workflow task began                      │ │
│  │ task.progress            # Progress update during task                │ │
│  │ task.completed           # A workflow task finished                   │ │
│  │ artifact.created         # An artifact was produced                   │ │
│  │ artifact.updated         # An artifact was modified                   │ │
│  │ artifact.ready           # An artifact is ready for use               │ │
│  │ chat.message_appended    # A chat message was added                   │ │
│  │ chat.run_complete        # A chat run finished                        │ │
│  │ runtime.decomposition_planned  # MFJ decomposition ready              │ │
│  │ runtime.fan_out_requested      # Parallel execution requested         │ │
│  │ runtime.fan_in_ready           # Parallel tasks completed             │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│  LAYER 3: CONTROL-PLANE EVENTS (Orchestration Layer)                         │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  Durable session-routing state and orchestration decisions.                  │
│  Emitted by runtime/orchestrator for control flow.                           │
│                                                                              │
│  Examples:                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ app.patch_requested          # Minor fix requested                    │ │
│  │ app.design_change_requested  # Design/schema change requested         │ │
│  │ app.feature_change_requested # Feature addition/change requested      │ │
│  │ app.core_change_requested    # Fundamental change requested           │ │
│  │ approval.required            # Human approval needed                  │ │
│  │ approval.granted             # Human approved                         │ │
│  │ approval.denied              # Human rejected                         │ │
│  │ session.resumed              # Workflow resumed from pause            │ │
│  │ session.terminated           # Workflow terminated                    │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Layer Separation Rules

| Layer | Owned By | Consumed By | Persistence |
|-------|----------|-------------|-------------|
| Domain | Modules | Workflows, Platform | Long-term (database) |
| Runtime Execution | Workflows/Agents | Orchestrator, UI, E2B | Session-scoped |
| Control-Plane | Orchestrator | Runtime, UI | Durable control state |

---

## 2. Complete Normalized Event Vocabulary

### Process Events

```yaml
process:
  started:
    description: "A workflow process has started"
    payload:
      process_id: string
      workflow_name: string
      context: object

  paused:
    description: "Process paused (awaiting input, approval, or external)"
    payload:
      process_id: string
      reason: enum[awaiting_input, awaiting_approval, external_dependency]

  resumed:
    description: "Process resumed after pause"
    payload:
      process_id: string

  completed:
    description: "Process finished successfully"
    payload:
      process_id: string
      result: object

  failed:
    description: "Process failed with error"
    payload:
      process_id: string
      error: object
```

### Task Events

```yaml
task:
  started:
    description: "A specific task within a workflow started"
    payload:
      task_id: string
      process_id: string
      task_name: string

  progress:
    description: "Progress update during task execution"
    payload:
      task_id: string
      progress: number  # 0-100
      message: string

  completed:
    description: "Task finished successfully"
    payload:
      task_id: string
      result: object

  failed:
    description: "Task failed"
    payload:
      task_id: string
      error: object

  awaiting_input:
    description: "Task needs user input to continue"
    payload:
      task_id: string
      input_schema: object
```

### Artifact Events

```yaml
artifact:
  created:
    description: "An artifact was created"
    payload:
      artifact_id: string
      artifact_type: string  # module, workflow, page, component, etc.
      process_id: string

  updated:
    description: "An artifact was modified"
    payload:
      artifact_id: string
      changes: object

  ready:
    description: "An artifact is ready for use/preview"
    payload:
      artifact_id: string
      bundle_path: string
```

### Chat Events

```yaml
chat:
  message_appended:
    description: "A message was added to the chat"
    payload:
      message_id: string
      role: enum[user, assistant, system, tool]
      content: string

  tool_call_requested:
    description: "Agent requested a tool call"
    payload:
      tool_name: string
      arguments: object

  tool_result_received:
    description: "Tool returned a result"
    payload:
      tool_name: string
      result: object

  handoff_requested:
    description: "Agent requested handoff to another agent"
    payload:
      from_agent: string
      to_agent: string

  run_complete:
    description: "Chat run finished"
    payload:
      process_id: string
      termination_reason: string
```

### Runtime Control Events

```yaml
runtime:
  decomposition_planned:
    description: "MFJ decomposition is ready for fan-out"
    payload:
      plan_id: string
      tasks: array  # List of decomposed tasks

  fan_out_requested:
    description: "Parallel task execution requested"
    payload:
      parent_id: string
      child_tasks: array

  fan_in_ready:
    description: "All parallel tasks completed"
    payload:
      parent_id: string
      results: array

  build_plan_created:
    description: "App build plan created"
    payload:
      plan_id: string
      spec: object

  validation_checkpoint:
    description: "Validation step completed"
    payload:
      checkpoint_name: string
      passed: boolean
      issues: array
```

### UI Tool Events

```yaml
ui.tool:
  requested:
    description: "UI tool interaction requested"
    payload:
      tool_type: string
      data: object

  responded:
    description: "User responded to UI tool"
    payload:
      tool_type: string
      response: object

  completed:
    description: "UI tool interaction completed"
    payload:
      tool_type: string
      result: object
```

---

## 3. MFJ (Mid-Flight Journey) Event Flow

### Critical Rule

> **MFJ must be triggered by runtime events, not by discovering structured outputs.**

### Correct MFJ Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        MFJ EVENT-DRIVEN FLOW                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  STEP 1: Decomposition Agent Executes                                        │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│    DecompositionAgent                                                        │
│           │                                                                  │
│           ├── produces structured output (internal)                          │
│           │                                                                  │
│           └── emits: runtime.decomposition_planned  ◄── EXPLICIT EVENT       │
│                          │                                                   │
│                          ▼                                                   │
│  STEP 2: Runtime Reacts                                                      │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│    Adapter captures event                                                    │
│           │                                                                  │
│           └── normalizes to: runtime.decomposition_planned                   │
│                          │                                                   │
│                          ▼                                                   │
│  STEP 3: Orchestrator Dispatches                                             │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│    WorkflowPackCoordinator                                                   │
│           │                                                                  │
│           ├── receives event                                                 │
│           │                                                                  │
│           └── triggers MFJ fan-out deterministically                         │
│                          │                                                   │
│                          ▼                                                   │
│  STEP 4: Parallel Execution                                                  │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                        │
│    │ Task A      │  │ Task B      │  │ Task C      │                        │
│    │ emit:       │  │ emit:       │  │ emit:       │                        │
│    │ task.started│  │ task.started│  │ task.started│                        │
│    │ ...         │  │ ...         │  │ ...         │                        │
│    │ task.done   │  │ task.done   │  │ task.done   │                        │
│    └─────────────┘  └─────────────┘  └─────────────┘                        │
│                          │                                                   │
│                          ▼                                                   │
│  STEP 5: Fan-In                                                              │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│    All child tasks complete                                                  │
│           │                                                                  │
│           └── emits: runtime.fan_in_ready                                    │
│                          │                                                   │
│                          ▼                                                   │
│  STEP 6: Parent Resumes                                                      │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│    Parent workflow resumes                                                   │
│           │                                                                  │
│           └── continues with aggregated results                              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### What NOT To Do

```
❌ WRONG: Scan transcript for structured output patterns
❌ WRONG: Parse text messages to find decomposition plans
❌ WRONG: Infer fan-out from presence of array outputs
❌ WRONG: Wait until run completion to check what happened

✅ RIGHT: React to explicit runtime.decomposition_planned event
✅ RIGHT: Decomposition agent emits event immediately when plan ready
✅ RIGHT: Runtime makes decision during execution, not after
```

---

## 4. Build Pipeline Event Flow

Every builder workflow MUST emit events at key checkpoints.

### Module Builder Example

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     MODULE BUILDER EVENT FLOW                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ModuleBuilder                                                               │
│        │                                                                     │
│        ├── emit: task.started                                                │
│        │         { task_name: "build_contacts_module" }                      │
│        │                                                                     │
│        ├── generate schema.py                                                │
│        │                                                                     │
│        ├── emit: task.progress                                               │
│        │         { progress: 30, message: "Schema generated" }               │
│        │                                                                     │
│        ├── generate actions.py                                               │
│        │                                                                     │
│        ├── emit: task.progress                                               │
│        │         { progress: 60, message: "Actions generated" }              │
│        │                                                                     │
│        ├── emit: artifact.created                                            │
│        │         { artifact_type: "module", name: "contacts" }               │
│        │                                                                     │
│        ├── validate module                                                   │
│        │                                                                     │
│        ├── emit: runtime.validation_checkpoint                               │
│        │         { passed: true }                                            │
│        │                                                                     │
│        └── emit: task.completed                                              │
│                  { result: { module: "contacts", files: [...] } }            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### UI Builder Example

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       UI BUILDER EVENT FLOW                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  UIBuilder                                                                   │
│        │                                                                     │
│        ├── emit: task.started                                                │
│        │         { task_name: "build_contacts_page" }                        │
│        │                                                                     │
│        ├── generate page schema                                              │
│        │                                                                     │
│        ├── emit: artifact.created                                            │
│        │         { artifact_type: "page", name: "contacts" }                 │
│        │                                                                     │
│        ├── emit: artifact.updated                                            │
│        │         { changes: { added_data_table: true } }                     │
│        │                                                                     │
│        ├── emit: artifact.ready  ◄── E2B reacts to this                      │
│        │         { preview_available: true }                                 │
│        │                                                                     │
│        └── emit: task.completed                                              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### All Builder Workflows Must Emit

| Event | When | Required |
|-------|------|----------|
| `task.started` | Task begins | Yes |
| `task.progress` | During execution | Optional |
| `artifact.created` | New artifact produced | Yes |
| `artifact.updated` | Artifact modified | When applicable |
| `artifact.ready` | Artifact ready for use | Yes |
| `runtime.validation_checkpoint` | Validation complete | When applicable |
| `task.completed` | Task finished | Yes |
| `task.failed` | Task failed | On failure |

---

## 5. Revision System Event Flow

Revisions MUST be routed via control-plane events.

### Revision Event Types

```yaml
# Control-plane revision events
app:
  patch_requested:
    description: "Minor fix that doesn't change architecture"
    routing: "targeted_update"
    examples:
      - "Fix typo in contact form label"
      - "Change button color"
      - "Fix validation error message"

  design_change_requested:
    description: "Visual, brand, layout, or UI-schema change that keeps the same product concept"
    routing: "design_refinement_or_schema_rebuild"
    examples:
      - "Switch the app to a premium dark theme"
      - "Rework the dashboard layout"
      - "Change navigation to a sidebar"

  feature_change_requested:
    description: "Add or modify a feature within existing architecture"
    routing: "partial_mfj_rebuild"
    examples:
      - "Add email field to contacts"
      - "Add export button to table"
      - "Add new page for reports"

  core_change_requested:
    description: "Fundamental change that requires re-planning"
    routing: "restart_value_engine"
    examples:
      - "Change from CRM to project management"
      - "Add multi-tenancy support"
      - "Switch data model entirely"
```

### Revision Routing Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       REVISION ROUTING FLOW                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  User Request: "Add email field to contacts"                                 │
│        │                                                                     │
│        ▼                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  CLASSIFIER                                                          │   │
│  │  Analyzes request and classifies change type                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│        │                                                                     │
│        └── emit: app.feature_change_requested                                │
│                  { scope: "contacts_module", change: "add_email_field" }     │
│                                                                              │
│        ▼                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  ROUTER                                                              │   │
│  │  Routes based on event type                                          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│        │                                                                     │
│        ├── patch_requested ──────► Targeted Update (single file)            │
│        │                                                                     │
│        ├── design_change_requested ─────► Design / Schema Re-entry          │
│        │                                                                     │
│        ├── feature_change_requested ──────► Scoped MFJ Rebuild              │
│        │         │                                                           │
│        │         └── emit: runtime.decomposition_planned (scoped)            │
│        │                   { affected: ["contacts_module", "contacts_page"] }│
│        │                                                                     │
│        └── core_change_requested ──────► Restart ValueEngine                │
│                  │                                                           │
│                  └── emit: process.started                                   │
│                            { workflow: "ValueEngine", fresh: true }          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

See [Refinement Control Plane](./REFINEMENT_CONTROL_PLANE_SPEC.md) for the
authoritative re-entry and persistence contract behind these events.

---

## 6. ValueEngine ↔ Build Loop Event Flow

The generation pipeline must be event-driven end-to-end.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   INTENT → APP PIPELINE (EVENT-DRIVEN)                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  1. VALUE ENGINE                                                     │   │
│  │     User intent → App concept                                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│        │                                                                     │
│        ├── emit: task.started { workflow: "ValueEngine" }                    │
│        │                                                                     │
│        ├── (concept development)                                             │
│        │                                                                     │
│        ├── emit: artifact.created { type: "app_concept" }                    │
│        │                                                                     │
│        └── emit: task.completed { result: concept }                          │
│                                │                                             │
│                                ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  2. DECOMPOSITION                                                    │   │
│  │     Concept → Build plan → Tasks                                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│        │                                                                     │
│        ├── emit: task.started { workflow: "Decomposition" }                  │
│        │                                                                     │
│        ├── (analyze concept, create build plan)                              │
│        │                                                                     │
│        ├── emit: runtime.build_plan_created { plan: ... }                    │
│        │                                                                     │
│        └── emit: runtime.decomposition_planned  ◄── TRIGGERS MFJ             │
│                  { tasks: [modules, ui, workflows, integrations] }           │
│                                │                                             │
│                                ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  3. MFJ FAN-OUT (Parallel Build)                                     │   │
│  │     Triggered by runtime.decomposition_planned event                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│        │                                                                     │
│        ├── emit: runtime.fan_out_requested                                   │
│        │                                                                     │
│        ├── ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐         │
│        │   │ Modules   │ │ UI Pages  │ │ Workflows │ │Integrations│         │
│        │   │ Builder   │ │ Builder   │ │ Builder   │ │ Builder   │         │
│        │   │           │ │           │ │           │ │           │         │
│        │   │ emits:    │ │ emits:    │ │ emits:    │ │ emits:    │         │
│        │   │ task.*    │ │ task.*    │ │ task.*    │ │ task.*    │         │
│        │   │ artifact.*│ │ artifact.*│ │ artifact.*│ │ artifact.*│         │
│        │   └───────────┘ └───────────┘ └───────────┘ └───────────┘         │
│        │         │             │             │             │                 │
│        │         └─────────────┴─────────────┴─────────────┘                 │
│        │                                │                                    │
│        └── emit: runtime.fan_in_ready   │                                    │
│                                         ▼                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  4. ASSEMBLY                                                         │   │
│  │     Combine artifacts → App bundle                                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│        │                                                                     │
│        ├── emit: task.started { workflow: "Assembly" }                       │
│        │                                                                     │
│        ├── (combine all artifacts)                                           │
│        │                                                                     │
│        ├── emit: artifact.ready { type: "app_bundle" }  ◄── E2B REACTS      │
│        │                                                                     │
│        └── emit: task.completed                                              │
│                                │                                             │
│                                ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  5. E2B PREVIEW                                                      │   │
│  │     Subscribes to artifact.ready events                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│        │                                                                     │
│        ├── on artifact.ready → update preview                                │
│        │                                                                     │
│        └── on artifact.updated → hot reload                                  │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════    │
│  REVISION LOOP (Event-Driven)                                                │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  User feedback: "Change the concept fundamentally"                           │
│        │                                                                     │
│        └── emit: app.core_change_requested                                   │
│                                │                                             │
│                                ▼                                             │
│                       RESTART AT STEP 1                                      │
│                       (ValueEngine new run)                                  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Adapter Responsibility

The execution adapter MUST iterate events in real-time.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      ADAPTER BEHAVIOR (REQUIRED)                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ✅ CORRECT ADAPTER BEHAVIOR:                                                │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│    start workflow                                                            │
│        │                                                                     │
│        ├── iterate events as they occur (not batch at end)                   │
│        │                                                                     │
│        ├── map AG2 events → normalized runtime events                        │
│        │                                                                     │
│        ├── map custom checkpoints → normalized runtime events                │
│        │                                                                     │
│        ├── emit normalized events IMMEDIATELY                                │
│        │                                                                     │
│        ├── allow gating/pause/abort DURING iteration                         │
│        │                                                                     │
│        └── finish with process completion events                             │
│                                                                              │
│  ❌ WRONG ADAPTER BEHAVIOR:                                                  │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│    start workflow                                                            │
│        │                                                                     │
│        ├── run to completion (black box)                                     │
│        │                                                                     │
│        ├── WAIT until end                                                    │
│        │                                                                     │
│        └── THEN interpret results                                            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Event Mapping (AG2 → Normalized)

| AG2 Event | Normalized Event |
|-----------|------------------|
| `GroupChatRunChatEvent` | `task.started` or `task.progress` |
| `TextEvent` | `chat.message_appended` |
| `ToolCallEvent` | `chat.tool_call_requested` |
| `ToolResponseEvent` | `chat.tool_result_received` |
| `InputRequestEvent` | `task.awaiting_input` |
| `TerminationEvent` | `process.paused` or `process.completed` |
| `RunCompletionEvent` | `chat.run_complete`, `process.completed` |
| `ErrorEvent` | `process.failed` |
| `DecompositionPlannedEvent` (custom) | `runtime.decomposition_planned` |
| `ArtifactPublishedEvent` (custom) | `artifact.ready` |
| `BuildPlanCreatedEvent` (custom) | `runtime.build_plan_created` |

---

## 8. E2B Event Subscription

E2B preview MUST follow the event stream, not wait for full completion.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        E2B EVENT SUBSCRIPTION                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  E2B Preview subscribes to:                                                  │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│  artifact.created    → Prepare for new artifact                              │
│  artifact.updated    → Hot reload artifact                                   │
│  artifact.ready      → Show/refresh preview                                  │
│  process.completed   → Final state                                           │
│                                                                              │
│  E2B Preview does NOT:                                                       │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│  ❌ Wait for full workflow completion only                                   │
│  ❌ Poll for static rebuild cycles                                           │
│  ❌ Rebuild everything on every change                                       │
│                                                                              │
│  EVENT-DRIVEN PREVIEW FLOW:                                                  │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│    Build Process                         E2B Preview                         │
│         │                                     │                              │
│         ├── artifact.created (module) ───────┼→ (prepare)                    │
│         │                                     │                              │
│         ├── artifact.ready (module) ─────────┼→ reload module                │
│         │                                     │                              │
│         ├── artifact.created (page) ─────────┼→ (prepare)                    │
│         │                                     │                              │
│         ├── artifact.updated (page) ─────────┼→ hot reload page              │
│         │                                     │                              │
│         ├── artifact.ready (page) ───────────┼→ show page                    │
│         │                                     │                              │
│         └── process.completed ───────────────┼→ final state                  │
│                                              │                              │
│                                         INCREMENTAL                          │
│                                         UPDATES                              │
│                                         (not batch)                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Source of Truth

### Explicit Declaration

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SOURCE OF TRUTH                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ✅ SOURCE OF TRUTH:                                                         │
│  • Normalized runtime events                                                 │
│  • Control-plane state                                                       │
│  • Persisted session state                                                   │
│  • Artifact state                                                            │
│                                                                              │
│  ❌ NOT SOURCE OF TRUTH:                                                     │
│  • Transcript text                                                           │
│  • Structured output discovery                                               │
│  • Message patterns                                                          │
│  • Inferred workflow state                                                   │
│                                                                              │
│  PERSISTENCE GUIDANCE:                                                       │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│  Persist canonically:                                                        │
│  • Session state                                                             │
│  • Control-plane state                                                       │
│  • Normalized runtime events                                                 │
│  • Artifact state                                                            │
│  • Structured outputs that matter for resume/replay                          │
│                                                                              │
│  Treat as secondary:                                                         │
│  • Raw transcript (replay support, debugging, UI rendering)                  │
│                                                                              │
│  NEVER treat as canonical:                                                   │
│  • Transcript shape                                                          │
│  • Text patterns                                                             │
│  • Output parsing results                                                    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 10. Constraints Summary

### DO NOT

| Constraint | Why |
|------------|-----|
| Trigger MFJ from structured output discovery | Breaks event-first model |
| Rely on text parsing or transcript shape | Fragile, non-deterministic |
| Mix domain events with runtime events | Layer violation |
| Treat workflows as black-box runs | Prevents real-time reaction |
| Delay orchestration decisions until run completion | Breaks streaming model |
| Wait until end of run to interpret results | Prevents incremental updates |

### MUST

| Requirement | Why |
|-------------|-----|
| Emit explicit events at checkpoints | Enables deterministic orchestration |
| React to events in real-time | Enables streaming execution |
| Keep event layers separate | Maintains architectural clarity |
| Iterate adapter events as they occur | Enables gating/pause/abort |
| Let E2B follow event stream | Enables incremental preview |
| Treat events as source of truth | Removes transcript dependence |

---

## Summary

This system behaves like:

| Property | Description |
|----------|-------------|
| **Streaming execution engine** | Events flow continuously, reactions happen in real-time |
| **Reactive system** | Orchestration responds to events, not polls for state |
| **Event-driven orchestrator** | All major transitions triggered by explicit events |

NOT like:

| Anti-pattern | Description |
|--------------|-------------|
| Batch LLM pipeline | Run to completion, then process results |
| Post-processing system | Infer what happened after the fact |
| Transcript parser | Derive state from text patterns |

### Key Event Flows

1. **MFJ**: `DecompositionAgent` → `runtime.decomposition_planned` → fan-out
2. **Build**: `task.started` → `artifact.created` → `artifact.ready` → `task.completed`
3. **Revision**: `app.*_change_requested` → router → targeted update or rebuild
4. **E2B**: subscribes to `artifact.*` → incremental preview updates
