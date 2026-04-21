---
title: Workflow Routing Transitions
status: Authoritative - Pre-Production, No Backward Compat
created: 2026-04-13
updated: 2026-04-20
depends_on: ui-systems.md, session-router.md
---

# Workflow Routing Transitions

The global workflow routing layer lives in
`mozaiks-platform/app/workflows/extended_orchestration/extension_registry.json`.

It is not agent routing, not app navigation, and not workflow-local MFJ.

## Contract

`extension_registry.json` has three concerns:

| Concern | Field | Owner | Purpose |
| --- | --- | --- | --- |
| Workflow registry | `workflows[]` | platform/workflow author | Known workflows and hard dependencies |
| Workflow sequencing | `workflow_sequences[]` | runtime | Auto-advance and transition checkpoints after workflow completion |
| Transition routing | `transitions[]` | platform shell author | User choices, context seeds, silent redirects, confirmations |

Workflow sequencing uses `workflow_sequences[]`.
Treat this data as workflow sequence metadata, not shell navigation or workflow-local MFJ.
If a sequence includes an entry transition as its first step, `entrypoints[]`
points to that transition for actual route entry.

Workflow-local MFJ lives in:

```text
<workflow>/extended_orchestration/mfj_extension.json
```

Do not put MFJ graphs in the global registry.

## Hard Rules

- For branded transition visuals, place transition components in `mozaiks-platform/app/workflows/extended_orchestration/ui/` and export them from `ui/index.js`.
- Keep transition UI files focused on transition screens only; keep shared runtime helpers in `chat-ui/src/platform`.
- Do not put top-level `component`, `config`, title, background, option label, or option description fields in product transition declarations.
- Do not put `entry_transition` on workflow sequence declarations.
- Do not put product-specific presets such as dogfood shortcuts in the global transition schema.
- Transition `context_variables` may seed deterministic context only when the target workflow declares those keys in `context_variables.yaml`.
- Use `entrypoints[]` entries with `transition: <id>` to enter a transition screen.

## Transitions

Transitions are router decisions. They may render UI, but the declaration stays
semantic and workflow-agnostic.

```json
{
  "id": "app_type_selector",
  "transition_type": "user_choice_context",
  "ui": {
    "component": "AppTypeSelector",
    "mode": "screen"
  },
  "route_to": "ValueEngine",
  "options": [
    {
      "id": "new_app",
      "context_variables": { "app_type": "new" }
    },
    {
      "id": "existing_app",
      "context_variables": { "app_type": "existing" }
    }
  ]
}
```

Supported target types:

- `route_to` can point to another transition id.
- `route_to` can point to a workflow id.
- The loader stamps `route_type` internally after validation.
- Transition UI emits `option_id`; it does not emit `route_to`.
- `user_choice_context` uses a shared top-level `route_to` unless an option overrides it.
- `user_choice_route` requires each option to declare `route_to`.
- `options[].context_variables` is merged into the accumulated transition context before target workflow creation.
- The workflow start path filters context against the target workflow's `context_variables.yaml` definitions.
- Keep transition declarations semantic. Put branded copy/images/layout in the
  React component file referenced by `ui.component`, not in transition option routing
  fields.

The shell renders transition UI through `TransitionScreen`. `ui.component` is a
registry key, not a file path. Built-in screens such as `LauncherScreen` and
`ConfirmScreen` are available for generic cases. For product-specific visuals,
create a workflow-local transition component and export it via:

```text
mozaiks-platform/app/workflows/extended_orchestration/ui/index.js
```

The registry owns routing and context semantics (`transition_type`, `route_to`,
`options[].route_to`, and `options[].context_variables`).

## Dependencies

Dependencies are hard prerequisites and belong on workflow entries:

```json
{
  "id": "AppGenerator",
  "dependencies": ["DesignDocs", "AgentGenerator"]
}
```

SessionRouter enforces these before workflow start. Dependencies are not cards,
tabs, or transition options by default.

## Workflow Sequences

`workflow_sequences[]` is the preferred field for ordered workflow auto-advance:

```json
{
  "id": "build",
  "description": "Full build pipeline.",
  "steps": [
    { "transition": "app_type_selector" },
    { "workflows": ["ValueEngine"] },
    { "transition": "coding_journey_selector" },
    { "workflows": ["DesignDocs"] },
    { "workflows": ["AgentGenerator"] },
    { "workflows": ["AppGenerator"] }
  ]
}
```

Rules:

- Use sequences only when completion of one phase should automatically start the next phase.
- Use one workflow per step for serial execution.
- Use multiple workflows in one step only when they should run as a parallel phase.
- Use `{ "transition": "<id>" }` when a completed workflow should pause on a surfaced checkpoint before the next workflow starts.
- Do not place a workflow in the same or earlier step as one of its required dependencies.
- If `B` depends on `A`, author the sequence as `A` in an earlier step and `B` in a later step.
- Do not list the same workflow more than once in a sequence.
- Entry UI belongs to `extension_registry.json -> entrypoints[]` via `transition`. The sequence may include the same transition as its first step so the journey contract remains fully ordered.

## Navigation Entry

The app shell enters a transition directly:

```json
{
  "entrypoints": [
    {
      "id": "create_app",
      "path": "/create",
      "label": "Create App",
      "sequence": "build",
      "transition": "app_type_selector"
    }
  ]
}
```

Optional header pills are declared separately in `shell.json`:

```json
{
  "header": {
    "pages": [
      { "path": "/create", "label": "Create App" }
    ]
  }
}
```

`RouteRenderer` mounts `TransitionScreen`, and transition resolution calls
`/api/transitions/resolve`. If the result is another transition, the shell mounts
that transition. If the result is a workflow, the backend creates a chat session
and the shell navigates to chat.

## Context Hydration

Transitions write deterministic key/value seeds into accumulated journey context.
The runtime does not mutate workflow files. Before a workflow chat session is
created, context is filtered against the target workflow's declared
`context_variables.yaml` definitions. AG2 receives only declared keys, and agent
visibility remains controlled by that workflow's `agents:` exposure map.

## Generator Rules

AgentGenerator may generate `extension_registry.json`, but it should default to:

- `workflows[]` with explicit dependencies
- `workflow_sequences[]` only when auto-advance sequencing is required
- `transitions: []` only when the generated app has no journey decisions

AgentGenerator may generate transition React stubs under
`extended_orchestration/ui` when transition visuals need product branding.
Persistent app pages still belong to AppGenerator schemas. Agent UI tools belong
to workflow-local `ui/` plus Python tools. If AgentGenerator emits a transition,
it must keep routing/context deterministic and bind `ui.component` to a
registered transition component key. Product-specific copy/images/layout belong
in the React stub.
