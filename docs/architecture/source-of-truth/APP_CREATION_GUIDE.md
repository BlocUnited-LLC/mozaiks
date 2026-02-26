# App Creation Guide

**Last updated:** 2026-02-26  
**Audience:** OSS developers and self-hosters  
**Prerequisites:** [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md)

This guide explains how to build a real app on mozaiks without mixing runtime layers, execution modes, or protocol scope.

---

## Scope

This is a public OSS implementation guide.

Out of scope for this doc set:

- private automation prompt chains
- organization-specific generator internals
- private deployment tooling details

---

## Mental Model

A mozaiks app is a **hybrid system**:

- AI workflows for reasoning/generation
- triggered actions for targeted updates
- plain app pages for CRUD/settings/dashboards

Artifacts connect these parts.

---

## Build Flow (6 Steps)

### Step 1: List capabilities

Start from one app intent sentence, then list capabilities as verb+noun.

Example:

1. Generate a content calendar
2. View calendar on a page
3. Edit posts
4. Refresh calendar
5. Create post series
6. Schedule posts
7. View analytics
8. Manage API connections
9. Configure posting preferences
10. Regenerate one post

### Step 2: Classify each capability by execution mode

| Mode | Use when | Entry |
|---|---|---|
| Mode 1: AI Workflow | conversational/multi-step reasoning | chat input |
| Mode 2: Triggered Action | one-shot update with or without AI | button/API trigger |
| Mode 3: Plain App | CRUD/settings/dashboard views | page navigation |

Verb heuristic:

- Mode 1: `generate`, `write`, `analyze`, `design`
- Mode 2: `refresh`, `retry`, `schedule`, `regenerate`
- Mode 3: `view`, `manage`, `configure`, `edit`

Tiebreaker: if back-and-forth conversation is required, use Mode 1.

### Step 3: Identify artifacts vs app data

Artifacts are persisted outputs shared across modes.

| Data object | Type | Created by | Read by | Updated by |
|---|---|---|---|---|
| ContentCalendar | Artifact | Mode 1 | Mode 3 | Mode 2 |
| Post | Artifact | Mode 1 | Mode 3 | Mode 2 |
| APIConnection | App data | Mode 3 | Mode 2/1 context | Mode 3 |
| PostingPreferences | App data | Mode 3 | Mode 1 context | Mode 3 |

### Step 4: Build implementation paths

#### Mode 1 -> Workflow content

Create workflow files in `workflows/{name}/backend` and UI components in `workflows/{name}/frontend/components`.

Minimum backend files for a non-trivial workflow:

- `orchestrator.yaml`
- `agents.yaml`
- `tools.yaml`
- `stubs/tools/*.py`
- `context_variables.yaml`

#### Mode 2 -> Trigger/action path

Implement:

- trigger source (button/API/scheduler)
- action endpoint/function
- optional mini-workflow if AI reasoning is required

#### Mode 3 -> App route/page path

Implement standard app pages/components and API handlers.

Typical pages:

- `/app/calendar`
- `/app/posts`
- `/app/settings/integrations`
- `/app/analytics`

### Step 5: Wire cross-references

Require explicit contracts between paths:

1. Mode 3 pages read artifacts produced by Mode 1.
2. Mode 2 triggers update those same artifacts.
3. Mode 1 context reads app data configured in Mode 3.

If these references are missing, the app feels like disconnected systems.

### Step 6: Define graph and dependency structures

Use decomposition outputs to derive:

- `graph_injection.yaml` (context injection and mutation rules)
- `_pack/workflow_graph.json` (journeys/gates across workflows)

Keep them separate:

- `graph_injection.yaml` = memory/context graph
- `_pack/workflow_graph.json` = workflow orchestration graph

---

## Minimal File Blueprint

```text
workflows/generate_calendar/
├── backend/
│   ├── orchestrator.yaml
│   ├── agents.yaml
│   ├── tools.yaml
│   ├── context_variables.yaml
│   ├── graph_injection.yaml
│   └── stubs/tools/save_calendar.py
└── frontend/
    ├── theme_config.json
    └── components/CalendarArtifact.tsx
```

Optional declarative business configs:

- `notifications.yaml` or `notifications/*.yaml`
- `subscription.yaml` or `subscription/*.yaml`
- `settings.yaml` or `settings/*.yaml`

---

## Verification Checklist

1. Mode classification complete for all capabilities.
2. Every Mode 1 output needed by UX exists as an artifact.
3. Mode 3 pages can render persisted artifact data without chat.
4. Mode 2 triggers update artifacts or app state intentionally.
5. `events.yaml` declarations are consumed by business YAML correctly.
6. `view` UI mode is not treated as sandbox runtime.
7. `graph_injection.yaml` and `_pack/workflow_graph.json` are not conflated.

---

## Common Failure Modes

| Failure | Symptom | Fix |
|---|---|---|
| Everything forced into Mode 1 | users must chat for normal CRUD | split into Mode 2/3 where appropriate |
| No artifact contract | AI outputs never appear in app pages | define artifact schemas and fetch paths |
| Missing cross-reference wiring | settings do not affect workflows | connect app data into `context_variables.yaml` |
| Protocol/runtime confusion | AG-UI compared to business billing rules | keep AG-UI as protocol scope only |
| Graph confusion | memory graph and workflow DAG mixed | keep separate files and responsibilities |

---

## Next Reading

- [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md)
- [UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md](UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md)
- [PROCESS_AND_EVENT_MAP.md](PROCESS_AND_EVENT_MAP.md)
- [GRAPH_INJECTION_CONTRACT.md](GRAPH_INJECTION_CONTRACT.md)
