# Pack Graph Semantics (v2 — Journeys + Gates)

This document defines the runtime meaning ("semantics") and taxonomy of `workflows/_pack/workflow_graph.json`.

`workflow_graph.json` is **app-agnostic**: it describes relationships between **workflow types** (templates).  
At runtime, those rules are evaluated inside a **scope** (this runtime calls the scope key `app_id`, but integrators can treat it as any stable isolation id).

This is a **v2 schema**: there are no `nodes`/`edges` and no legacy fields.

---

## 1) Core Taxonomy

### Workflow Type (template)
- Identified by `id` (e.g., `ValueEngine`, `ValidationEngine`)
- Backed by a workflow folder under `workflows/<id>/`

### Chat Session (run instance)
- Identified by `chat_id`
- Represents one run of a workflow type
- Stores status (`IN_PROGRESS` / `COMPLETED`) and transcript/artifacts

### Scope (isolation boundary)
- Identified by `app_id` in HTTP/WS APIs
- Meaning is **integrator-defined**:
  - Mozaiks: `app_id` = the App being built/managed
  - Others: `app_id` could be workspace id, repo id, project id, ticket id, etc.
- Used to prevent cross-scope state bleed and answer questions like: “Has workflow X completed for this scope?”

### Journey (wizard chain)
- An ordered sequence of workflows meant to feel seamless to the user.
- Journeys can enforce step order (gating) and can auto-advance on completion.

### Gate (prerequisite rule)
- A directed prerequisite relationship between two workflow types.
- Gates can be `required` (enforced) or `optional` (informational).

---

## 2) Schema (workflows/_pack/workflow_graph.json)

Top-level shape:

```json
{
  "pack_name": "DefaultPack",
  "version": 2,
  "workflows": [{ "id": "ValueEngine", "type": "primary", "description": "..." }],
  "journeys": [{
    "id": "build",
    "label": "Build App",
    "scope": "app",
    "enforce_step_gating": true,
    "auto_attach_on_start": true,
    "auto_advance": true,
    "steps": ["ValueEngine", "AgentGenerator", "AppGenerator"]
  }],
  "gates": [{
    "from": "AppGenerator",
    "to": "ValidationEngine",
    "gating": "required",
    "scope": "app",
    "reason": "ValidationEngine requires the app to be built first."
  }]
}
```

### 2.1 `workflows[]`
Informational registry of workflow types for UI/docs:
- `id` (string, required): workflow type id; must match workflow folder name.
- `type` (string, optional): informational taxonomy for UI/docs (e.g. `primary`, `dependent`, `independent`).
- `description` (string, optional): human-readable.

### 2.2 `journeys[]`
Defines wizard chains:
- `id` (string, required): journey key (stable id, referenced in persisted sessions).
- `label` (string, optional): display label.
- `steps` (string[], required): ordered workflow ids.
- `scope` (string, optional): scope for implicit step prerequisites (`user` default, or `app`).
- `enforce_step_gating` (bool, optional): if true, the runtime creates **implicit required gates** between consecutive steps.
- `auto_attach_on_start` (bool, optional): if true, starting `steps[0]` attaches a new journey instance to the chat session.
- `auto_advance` (bool, optional): if true, completing a step will seamlessly start the next step.

### 2.3 `gates[]`
Defines cross-workflow prerequisites:
- `from` (string, required): upstream workflow id.
- `to` (string, required): downstream workflow id.
- `gating` (string, required): `required` | `optional`.
- `scope` (string, optional): `user` (default) or `app`.
- `reason` (string, optional): user-facing message when blocked.

---

## 3) Runtime Semantics

### 3.1 Required gating (“can I start this workflow?”)

For any `gates[]` entry where:
- `gating: "required"`

the runtime blocks starting/resuming `to` until `from` has at least one **COMPLETED** chat session inside the same scope.

`scope` controls *who* must have completed the prerequisite:
- `scope:"user"`: prerequisite must be completed by the same `user_id` in this `app_id`.
- `scope:"app"`: prerequisite can be completed by any user within this `app_id`.

Important: gating is satisfied by **any completed upstream run** (not “the latest run must be completed”).  
This prevents refactors/new attempts from re-locking downstream workflows.

Enforcement is defense-in-depth:
- Start: `POST /api/chats/{app_id}/{workflow_name}/start` (409 on missing prereqs)
- WebSocket connect: `/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}` (sends `chat.error` then closes)
- In-WS workflow start commands (e.g., `chat.start_workflow`, artifact “launch_workflow”) are also gated

### 3.2 Journey step gating

If a journey sets:
- `enforce_step_gating: true`

the runtime treats each consecutive pair as an implicit `required` gate:
- `steps[i-1] -> steps[i]`

### 3.3 Auto-advance (“seamless wizard”)

If a journey sets:
- `auto_advance: true`

then when a chat session for `steps[i]` completes, the runtime:
- creates (or reuses) a `ChatSession` for `steps[i+1]` in the same scope
- switches the UI to the new `chat_id` (`chat.context_switched`)
- auto-starts the next workflow if its `startup_mode` is `AgentDriven`

### 3.4 Persisted journey fields

The runtime stores journey metadata on chat sessions:
- `journey_id`: journey instance id (opaque uuid)
- `journey_key`: journey definition id (e.g. `build`)
- `journey_step_index`: 0-based index into `steps[]`
- `journey_total_steps`: optional total for display/debug

---

## 4) Mozaiks Example (Build Journey + ValidationEngine Gate)

Configuration intent:
- Build journey: `ValueEngine -> AgentGenerator -> AppGenerator` (auto-advance, app-scoped)
- Gate: `AppGenerator` required before `ValidationEngine` can start (app-scoped)

UX outcome:
- The build feels like one continuous wizard.
- ValidationEngine can’t start until the app is built.
- Multiple apps (different `app_id`) remain isolated; no cross-app state bleed.

---

## 5) Notes (avoid confusion)

- `workflows/_pack/workflow_graph.json` is the **macro** config (journeys + gates).
- Per-workflow decomposition (nested child chats) may use `workflows/<WorkflowName>/_pack/workflow_graph.json` and is a separate contract.
