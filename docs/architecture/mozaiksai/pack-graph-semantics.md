# Pack Graph Semantics (v3 - Workflow Routing)

This document defines the runtime meaning ("semantics") and taxonomy of
`workflows/extended_orchestration/extension_registry.json`.

`extension_registry.json` is **app-agnostic**: it describes relationships between **workflow types** (templates).
At runtime, those rules are evaluated inside a **scope** (this runtime calls the scope key `app_id`, but integrators can treat it as any stable isolation id).

This is a **v3 schema**: there are no `nodes`/`edges` and no alternate historical fields.

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

### Workflow Sequence
- An ordered sequence of workflow phases meant to auto-advance after completion.
- JSON field: `workflow_sequences[]`.
- This is not workflow-local MFJ.

### Dependency (prerequisite rule)
- A directed prerequisite relationship between two workflow types.
- Dependencies can be `required` (enforced) or `optional` (informational).

---

## 2) Schema (workflows/extended_orchestration/extension_registry.json)

Top-level shape:

```json
{
  "pack_name": "DefaultPack",
  "version": 3,
  "workflows": [
    { "id": "ValueEngine", "description": "..." },
    { "id": "AppGenerator", "dependencies": ["ValueEngine"] }
  ],
  "workflow_sequences": [{
    "id": "build",
    "description": "Build App",
    "steps": [
      { "workflows": ["ValueEngine"] },
      { "workflows": ["AppGenerator"] }
    ]
  }],
  "transitions": [{
    "id": "choose_path",
    "transition_type": "user_choice",
    "ui": { "component": "LauncherScreen", "mode": "screen", "shell_mode": "focused" },
    "options": [
      { "id": "build", "route_to": "ValueEngine" }
    ]
  }]
}
```

### 2.1 `workflows[]`
Informational registry of workflow types for UI/docs:
- `id` (string, required): workflow type id; must match workflow folder name.
- `type` (string, optional): informational taxonomy for UI/docs (e.g. `primary`, `dependent`, `independent`).
- `description` (string, optional): human-readable.
- `dependencies` (string[] or object[], optional): hard prerequisites enforced before workflow start.

### 2.2 `workflow_sequences[]`
Defines runtime auto-advance sequences:
- `id` (string, required): sequence key (stored in persisted session metadata).
- `description` (string, optional): human-readable.
- `steps` (list, required): ordered groups shaped as `{ "workflows": ["WorkflowId"] }` or surfaced checkpoints shaped as `{ "transition": "TransitionId" }`.

Workflow sequence declarations should be authored as sequence metadata only.
Entry UI belongs to `entrypoints[]` routes that reference `transition: <id>` directly.

Required dependencies must be serial in a sequence. If `B` depends on `A`,
`A` must appear in an earlier step than `B`. Same-step groups are reserved for
parallel phases with no required dependency edge between siblings.
Each workflow may appear at most once in a sequence.

### 2.3 `entrypoints[]`
Defines workflow-owned shell routes:
- `id` (string, required): stable route id.
- `path` (string, required): browser route such as `/create`.
- `label` (string, optional): user-facing route label.
- exactly one of `transition` or `workflow`.
- `sequence` (string, optional): sequence this entrypoint starts.

Use entrypoints only for routes that enter workflows or transition screens.
Persistent product pages are discovered from page/UI owner manifests.
Entrypoints that mount transition UI should usually set
`meta.shellMode: "focused"` so the shell suppresses footer and mobile bottom
navigation while the routing choice is active.

### 2.4 `transitions[]`
Defines router decision points:
- `id` (string, required): transition id.
- `transition_type` (string, required): `user_choice`, `user_choice_context`, `user_choice_route`, `condition`, `confirm`, `silent`, `progress_view`, or `prerequisite_redirect`.
- `ui` (object, required for `user_choice` and `confirm`): registered shell component binding, never a file path.
- `ui.shell_mode` (string, optional): shell chrome mode while the transition is
  active. Use `focused` for choice, setup, approval, and review screens unless
  the transition intentionally needs normal product chrome.
- `options` (list, optional): selectable targets for `user_choice`; each option declares its own `route_to`, the UI emits option id, and the router resolves the target.
- `options[].sequence` (string, optional): explicit sequence override for the selected branch.
- `route_to` (string, optional): direct target for single-route transitions.
- `sequence` (string, optional): explicit sequence binding for a single-route transition.
- `options[].context_variables` (object, optional): deterministic context seeds merged when the option is selected. The target workflow receives only keys declared in its `context_variables.yaml`.

---

## 3) Runtime Semantics

### 3.1 Required gating (“can I start this workflow?”)

For any required `workflows[].dependencies` entry, the runtime blocks
starting/resuming the dependent workflow until the upstream workflow has at
least one **COMPLETED** chat session inside the same scope.

`scope` controls *who* must have completed the prerequisite:
- `scope:"user"`: prerequisite must be completed by the same `user_id` in this `app_id`.
- `scope:"app"`: prerequisite can be completed by any user within this `app_id`.

Important: gating is satisfied by **any completed upstream run** (not “the latest run must be completed”).  
This prevents refactors/new attempts from re-locking downstream workflows.

Enforcement is defense-in-depth:
- Start: `POST /api/chats/{app_id}/{workflow_name}/start` (409 on missing prereqs)
- WebSocket connect: `/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}` (sends `chat.error` then closes)
- In-WS workflow start commands (e.g., `chat.start_workflow`, artifact “launch_workflow”) are also gated

### 3.2 Workflow sequence auto-advance

When a workflow in a sequence step completes, the runtime checks whether the
whole step group is complete. If so, it starts the next step group.

- It creates or reuses a `ChatSession` for the next step group.
- switches the UI to the new `chat_id` (`chat.context_switched`)
- auto-starts the next workflow through the same transport connection
- if the next sequence step is `{ "transition": "<id>" }`, emits
  `chat.transition_requested`; the shell mounts the registered transition React
  component and resumes routing through `/api/transitions/resolve`.

When a shell route enters a transition or workflow directly, it forwards the
route's `sequence` as `journey_id`. Transition options can override that active
journey by declaring their own `options[].sequence` value.

### 3.3 Persisted sequence fields

The runtime stores sequence metadata on chat sessions using the current
`journey_*` field names:

- `journey_instance_id`: sequence instance id (opaque uuid)
- `journey_key`: sequence definition id (e.g. `build`)
- `journey_position`: 0-based index into `steps[]`
- `journey_total_steps`: total step count

---

## 4) Mozaiks Example

Configuration intent:
- Build sequence: `app_type_selector -> ValueEngine -> ThemeCapture -> coding_journey_selector -> database_setup_selector -> DesignDocs -> AgentGenerator -> AppGenerator`
- Brownfield adoption sequence: `app_type_selector -> ExistingAppDiscovery`
- Dependencies: `DesignDocs` requires `ValueEngine`; generators require `DesignDocs`
- Entry transition: `/create` points directly at `app_type_selector`

UX outcome:
- The user picks a route before any workflow starts.
- The greenfield and brownfield choices can bind different authored sequences from the same entry transition.
- The runtime enforces prerequisites before each workflow start.
- Completion of one build phase can auto-advance to the next phase.
- Multiple apps (different `app_id`) remain isolated; no cross-app state bleed.

---

## 5) Notes (avoid confusion)

- `workflows/extended_orchestration/extension_registry.json` is the global config.
- Per-workflow decomposition uses `workflows/<WorkflowName>/extended_orchestration/mfj_extension.json`.
- Do not confuse global workflow sequences with workflow-local MFJ graphs.
