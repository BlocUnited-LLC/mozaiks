# Orchestration & Decomposition (Source of Truth)

**Status**: Active  
**Date**: December 16, 2025  
**Goal**: Define *when* and *how* MozaiksAI chooses between single-workflow execution, nested sub-chats, workflow packs, and multi-pass generation.

---

## Definitions (non-negotiable)

- **Workflow**: A single AG2 GroupChat run (one orchestration session) backed by one workflow folder: `workflows/{workflow_name}/`.
- **Module**: A logical stage inside a workflow strategy. Modules are design-time structure; runtime can still be one GroupChat unless/until multi-module execution is implemented.
- **Nested GroupChat**: An AG2-native sub-chat created *within* a running workflow (e.g., via `GroupChatTarget`). Returns a summary to the parent chat.
- **Workflow Pack**: Multiple workflows coordinated by a pack graph (`workflow_graph.json`). Each workflow runs in its *own* chat lifecycle.
- **Multi-Pass Generation**: A meta-orchestrator runs the generator workflow N times to produce N workflows, then assembles them into a pack.

---

## The Four Orchestration Approaches

| Approach | What it is | Who decides | When to use |
|---|---|---|---|
| **0. Multi-Pass Generation** | Run generator N times (wizard) | Meta-orchestrator (Foundry/Wizard) | “Build me a company/platform” → multiple capabilities need full generation passes |
| **1. Single Workflow** | One AG2 GroupChat + handoffs | PatternAgent (design-time) | Most requests; one interaction model; shared context; unified HITL |
| **2. Nested GroupChat** | Sub-team inside a workflow | Runtime agent (during execution) | Self-contained sub-task needing specialists; keep parent workflow intact |
| **3. Multi-Workflow Pack** | Multiple workflows + macro graph | PatternAgent (design-time) | Fundamentally different interaction models/lifecycles/HITL/security boundaries |

---

## Decision Hierarchy

### Q0: Is this a *compound* request requiring multiple generation passes?

If YES → **Approach 0 (Multi-Pass Generation)**.

Examples:
- “Build me a SaaS company”
- “Create an entire platform with auth, billing, admin, analytics”

If NO → proceed to PatternAgent design-time decisions.

#### Approach 0 example ("Build me a SaaS")

Multi-pass generation means the generator workflow runs multiple times (same flow, different scoped inputs), and the outputs are assembled into a single coordinated pack.

Example capability breakdown:

- Auth & onboarding
- Core product workflow
- Billing & subscription
- Admin dashboard
- Reporting/analytics

Output shape:

```
workflows/
├── AuthWorkflow/
├── CoreProductWorkflow/
├── BillingWorkflow/
├── AdminWorkflow/
├── ReportingWorkflow/
└── _pack/
  └── workflow_graph.json
```

### Q1: Can this be served by a single interaction model + lifecycle?

If YES → **Approach 1 (Single Workflow)**.

If NO → **Approach 3 (Multi-Workflow Pack)**.

### Runtime (within a workflow): Does a sub-task need its own coordinated sub-team?

If YES → **Approach 2 (Nested GroupChat)**.

#### Approach 2 note (AG2-native)

Nested GroupChats are an AG2 mechanism for a sub-team to work in an isolated sub-conversation and then return a summary back to the parent workflow.

---

## PatternAgent Decomposition Logic (Design-Time)

PatternAgent (Layer 0) decides whether:
- `is_multi_workflow: false` → generate **one** workflow (Approach 1)
- `is_multi_workflow: true` → generate **multiple** workflows + a pack graph (Approach 3)

### PatternSelection: minimal shape

```yaml
PatternSelection:
  # Core decomposition decision
  is_multi_workflow: boolean
  decomposition_reason: string | null

  # Pack identity (when multi-workflow)
  pack_name: string

  # Workflow list (1 for single, N for multi)
  workflows:
    - name: string
      role: "primary" | "dependent" | "independent"
      description: string
      pattern_id: integer
      pattern_name: string
```

### When to Decompose (`is_multi_workflow: true`)

Decompose when *any* of these are true:

1. **Incompatible interaction models**
   - Example: user-driven chat + scheduled batch job.

2. **Independent lifecycles**
   - Example: onboarding (one-time per user) vs reporting (on-demand) should be independently accessible.

3. **Different HITL requirements**
   - Example: drafting requires human approval loops; publishing should be automated.

4. **Scalability / security isolation requirements**
   - Example: finance vs marketing must not share state; or high-volume flows should be isolated from admin tooling.

### When NOT to Decompose (`is_multi_workflow: false`)

Keep a single workflow when:
- All agents serve one cohesive goal
- Differences are just *stages* (modules), not independent domains
- Shared state is the primary product feature
- One pattern clearly fits end-to-end

---

## Modules vs Workflows (avoid ambiguity)

| Concept | Single Workflow | Multi-Workflow Pack |
|---|---|---|
| Modules | Logical stages within one workflow | Each workflow can have its own modules |
| State | `context_variables.json` (workflow-local) | Pattern context (computed from persistence: app_id/user_id + per-workflow chat_id/status) + per-workflow context |
| Runtime | One chat session | Multiple chat sessions, pack coordinates |

---

## Pack Graph Semantics (`workflow_graph.json`)

The pack graph is a repo-owned **macro** config that defines:
- **Journeys** (wizard chains) for seamless auto-advance
- **Gates** (prerequisites) for blocking workflows until requirements are met

Example (v2):

```json
{
  "version": 2,
  "workflows": [{ "id": "SalesWorkflow", "type": "primary" }],
  "journeys": [
    {
      "id": "onboarding",
      "scope": "user",
      "enforce_step_gating": true,
      "auto_attach_on_start": true,
      "auto_advance": true,
      "steps": ["SalesWorkflow", "AnalyticsWorkflow"]
    }
  ],
  "gates": [
    { "from": "SalesWorkflow", "to": "AnalyticsWorkflow", "gating": "required", "scope": "user" }
  ]
}
```

Full contract: `docs/source_of_truth/PACK_GRAPH_SEMANTICS.md`.

---

## Pack Structure (Macro vs Micro)

```
workflows/
├── _pack/                        # MACRO (journeys + gates)
│   ├── workflow_graph.json
│   └── workflow_graph.dev.json   # optional (override via PACK_GRAPH_PATH)
├── ValueEngine/
│   ├── agents.json
│   ├── orchestrator.json
│   ├── handoffs.json
│   ├── tools.json
│   ├── context_variables.json
│   ├── structured_outputs.json
│   └── _pack/                    # optional (nested child chats for this workflow)
│       └── workflow_graph.json
└── ValidationEngine/
    └── ...
```

---

## Task Decomposition ("OpenRouter")

Task decomposition is a *runtime-capable* concept that can be attached as a lifecycle hook or invoked by an agent to break down a complex request into sub-tasks.

Important: this is distinct from PatternAgent pack decomposition.

- **PatternAgent decomposition**: design-time structure of workflows (single vs pack)
- **Task decomposition**: runtime breakdown of tasks (session/workflow/hybrid spawn modes)

### Decomposition Hook: minimal contract

A decomposition tool populates a standardized context payload:

```json
{
  "decomposition": {
    "triggered": true,
    "timestamp": "<iso-8601>",
    "original_request": "...",
    "analysis": {
      "is_complex": true,
      "reasoning": "..."
    },
    "sub_tasks": [
      {
        "id": 1,
        "name": "...",
        "description": "...",
        "suggested_pattern_id": 6,
        "dependencies": []
      }
    ],
    "execution_plan": {
      "execution_mode": "sequential",
      "spawn_mode": "session"
    }
  }
}
```

Spawn modes:
- **session**: execute sub-tasks within the same chat session via handoffs
- **workflow**: spawn new workflows per sub-task
- **hybrid**: mix session + workflow spawns

---

## Practical Guidance (defaults)

- Default to **Approach 1 (Single Workflow)**.
- Use **Approach 2 (Nested GroupChat)** for sub-teams inside a workflow.
- Use **Approach 3 (Multi-Workflow Pack)** only when interaction model/lifecycle/HITL/security require separation.
- Use **Approach 0 (Multi-Pass Generation)** for “build a company/platform” asks.
