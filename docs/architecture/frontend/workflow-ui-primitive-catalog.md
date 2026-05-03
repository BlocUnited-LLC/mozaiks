# Workflow UI Primitive Catalog

Mozaiks needs a **workflow UI catalog** that is narrower and more intentional than
"generate arbitrary React."

The catalog serves two different jobs:

1. **Shell-owned workflow status UI**
2. **Workflow-owned interaction and review UI**

Those are not the same surface contract and should not be generated the same way.

---

## Why This Exists

AG-UI and CopilotKit feel clean because they expose a small number of obvious UI
patterns. Mozaiks needs the same discipline, but without collapsing persistent app
pages, workflow UI, and shell execution status into one contract.

The canonical rule is:

- plain natural-language workflow reply uses the chat composer
- structured workflow interaction uses a workflow UI primitive
- workflow execution status uses shell-owned status primitives
- persistent app UI stays in page schemas and `workflow_touchpoints`

---

## Catalog Split

### Shell-Owned Workflow Status Primitives

These are framework-owned. They should not be generated as workflow-local React
components.

| Primitive | Purpose | Canonical owner | Realization |
|-----------|---------|-----------------|-------------|
| `run_status_banner` | Show running, paused, failed, completed | shell | built-in |
| `progress_stepper` | Show named workflow stages and current progress | shell | built-in |
| `agent_activity_feed` | Show background agent activity, handoffs, and work in progress | shell | built-in |
| `awaiting_reply_banner` | Show that the next composer reply goes to a pending workflow checkpoint | shell | built-in |

Rules:

- These are driven by runtime status, workflow stages, and execution telemetry.
- AgentGenerator should not emit them inside `ToolPlanning.ui_requirements`.
- AppGenerator should not try to recreate them as persistent page widgets.

### Plannable Workflow Interaction Primitives

These represent actual workflow checkpoints or review surfaces.

| Primitive | Default display | Use when |
|-----------|-----------------|----------|
| `composer_reply` | `composer` | The user just needs to answer in natural language |
| `approval_card` | `inline` | Approve, reject, or request revision |
| `choice_picker` | `inline` | Choose from bounded options |
| `confirmation_summary` | `inline` | Confirm captured facts or request edits |
| `form_card` | `inline` | Structured multi-field input |
| `record_picker` | `artifact` | Search/select records or entities |
| `file_upload_prompt` | `inline` | Upload one or more files |
| `diff_review` | `artifact` | Review before/after changes |
| `action_plan_review` | `artifact` | Review and approve a generated plan |
| `artifact_workbench` | `artifact` | Review a multi-pane artifact workspace with preview/export/actions |
| `document_preview` | `artifact` | Read-only document/report preview |
| `download_center` | `artifact` | Download generated files or bundles |
| `comparison_board` | `artifact` | Compare options or variants side by side |
| `diagram_viewer` | `artifact` | Review diagrams, sequences, or maps |
| `table_review` | `artifact` | Review row-based data with actions |

Rules:

- `composer_reply` is shell-owned input UX but still a plannable workflow checkpoint.
- All other entries are **workflow interaction patterns**.
- The primitive id is the canonical planning contract.
- `component` is the realization detail.
- `ui.workflow_primitive` is required on every emitted `UI_Tool` and `UI_Surface` manifest entry.
- Only renderable workflow primitives may appear in `tools.yaml`; shell status primitives and `composer_reply` must not.

### Shipped Shared Workflow Components

Some renderable workflow primitives now resolve to shipped shared components in
`chat-ui/src/core/ui/`.

| Primitive | Canonical shared component | Realization |
|-----------|----------------------------|-------------|
| `approval_card` | `ApprovalCard` | shipped component |
| `choice_picker` | `ChoicePicker` | shipped component |
| `confirmation_summary` | `ConfirmationSummary` | shipped component |
| `form_card` | `FormCard` | shipped component |
| `artifact_workbench` | `ArtifactWorkbench` | shipped component |
| `download_center` | `DownloadCenter` | shipped component |
| `diagram_viewer` | `DiagramViewer` | shipped component |

Rules:

- Prefer the canonical shipped component name directly in `ui.component`.
- Do not generate bespoke React for these when the shipped component already fits.
- Generate only a thin wrapper or re-export when workflow-local naming or light customization is required.
- Keep `primitives_hint` empty when no workflow-local wrapper is needed.

---

## Build Workflow Integration

The build path is intentionally split by ownership.

### 1. ToolPlanningAgent

`ToolPlanningAgent` is where workflow interaction shape is chosen.

Canonical output:

- `ToolPlanning.ui_requirements[*].workflow_primitive`
- `display`
- `component`
- `primitives_hint`

Rules:

- Use `composer_reply` for plain text feedback.
- Use a structured workflow primitive for bounded interaction.
- Do not emit shell status primitives here.

This is the **decision point** for workflow UI.

### 2. `tool_planning(...)` Normalization

`factory_app/workflows/AgentGenerator/tools/tool_planning.py` validates and caches:

- `primitives_hint` against the shipped component primitive registry
- `workflow_primitive` against the canonical workflow UI catalog
- `composer_reply` normalization:
  - force `display=composer`
  - force `component=null`
  - clear `primitives_hint`
- shipped component normalization:
  - default `component` to the canonical shipped component name when the primitive maps to one
  - clear `primitives_hint` when the canonical shipped component is used directly

This is the **contract enforcement point**.

### 3. ToolsManagerAgent

`ToolsManagerAgent` converts planning into `tools.yaml` / `lifecycle_tools`.

Rules:

- Match `ui_requirements` to tool entries by `tool`.
- If `workflow_primitive=composer_reply`:
  - do not emit `UI_Tool`
  - do not emit generated React
  - let the runtime input-request/composer lane handle the checkpoint
- Otherwise:
  - emit `ui.component`
  - emit `ui.mode`
  - emit `ui.workflow_primitive`
  - emit `ui_contract` when the surface is interactive

For shipped shared components:

- it is valid for `ui.component` to be the canonical shipped component name
- no workflow-local React file is required unless a wrapper is intentionally introduced

This is the **manifest synthesis point**.

### 4. UIFileGenerator

`UIFileGenerator` consumes `ToolsManifest`.

Rules:

- Treat `ui.workflow_primitive` as the source of truth for behavior.
- Treat `ui.component` as either a canonical shipped shared component name or a workflow-local implementation name.
- Generate React only for entries that actually produce workflow-local UI.
- If the primitive maps to a shipped shared component and `ui.component` already uses that name, skip React generation entirely.
- If the primitive maps to a shipped shared component but `ui.component` is workflow-local, generate only a thin wrapper/re-export.
- Do not generate files for `composer_reply`.

This is the **component realization point**.

### 5. Runtime / Shell

The runtime and shell own:

- `chat.tool_call`
- `tool_call_response`
- composer-mode input routing
- progress/status/activity shell UI

These are not AppGenerator responsibilities.

### 6. Acceptance Harness

Before treating the primitive catalog as stable for generated workflows, the repo
should prove one deterministic path that exercises the canonical lanes together.

That first-party target now lives at:

- `factory_app/workflows/WorkflowPrimitiveAcceptance`

It intentionally validates:

- `composer_reply` via the shell-owned composer lane
- `approval_card` via a real interactive `UI_Tool`
- `diagram_viewer` via a real one-way `UI_Surface`

Canonical smoke command:

```bash
python scripts/run_live_mfj_smoke.py \
  --workflow WorkflowPrimitiveAcceptance \
  --workflows-root factory_app/workflows \
  --tool-response-file factory_app/workflows/WorkflowPrimitiveAcceptance/smoke_responses.json
```

This acceptance workflow belongs in the generator/frontend validation loop, not
in the live app bundle. When AgentGenerator prompt, manifest, or runtime
contracts change, this workflow should keep passing before those changes are
considered promotion-ready.

---

## Relationship To AppGenerator

AppGenerator should not generate workflow UI primitives directly.

AppGenerator owns:

- persistent page schemas
- `workflow_touchpoints`
- page actions with `action_type: workflow`

AgentGenerator owns:

- workflow-local interactive UI
- workflow-local artifact UI
- generated workflow React stubs when needed

The page layer may **launch** a workflow, but the session UX after launch belongs
to the shell and AgentGenerator lane.

---

## Current Canonical Source

The current source of truth is split across:

- [workflow_ui_catalog.py](../../../mozaiksai/core/workflow/workflow_ui_catalog.py)
- [tool_planning.py](../../../factory_app/workflows/AgentGenerator/tools/tool_planning.py)
- [AgentGenerator prompts](../../../factory_app/workflows/AgentGenerator/agents.yaml)

If those contracts change, update the docs, prompts, validators, and tests
together.
