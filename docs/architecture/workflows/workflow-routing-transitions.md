---
title: Workflow Routing Transitions
status: Authoritative - Pre-Production
created: 2026-04-13
updated: 2026-04-20
depends_on: ../frontend/ui-system/generated-frontend-surface-contract.md, session-router.md
---

# Workflow Routing Transitions

The global workflow routing layer lives in
`factory_app/workflows/extended_orchestration/extension_registry.json`.
App/workspace roots may overlay it with their own
`workflows/extended_orchestration/extension_registry.json`.

It is not agent routing, not app navigation, and not workflow-local task batching.

## Contract

`extension_registry.json` has three concerns:

| Concern | Field | Owner | Purpose |
| --- | --- | --- | --- |
| Workflow registry | `workflows[]` | platform/workflow author | Known workflows and hard dependencies |
| Workflow sequencing | `workflow_sequences[]` | runtime | Auto-advance and transition checkpoints after workflow completion |
| Transition routing | `transitions[]` | platform shell author | User choices, context seeds, silent redirects, confirmations |

Workflow sequencing uses `workflow_sequences[]`.
Treat this data as workflow sequence metadata, not shell navigation or workflow-local task batching.
If a sequence includes an entry transition as its first step, `entrypoints[]`
points to that transition for actual route entry.

Workflow-local task batching lives in:

```text
<workflow>/extended_orchestration/task_batches.yaml
```

Do not put task batch graphs in the global registry.

## Hard Rules

- For shared build transition visuals, place transition components in `factory_app/workflows/extended_orchestration/ui/` and export them from `ui/index.js`.
- Keep transition UI files focused on transition screens only; keep shared runtime helpers in `chat-ui/src/platform`.
- Do not put top-level `component`, `config`, title, background, option label, or option description fields in product transition declarations.
- Do not put `entry_transition` on workflow sequence declarations.
- Do not put product-specific presets such as dogfood shortcuts in the global transition schema.
- Transition `context_variables` may seed deterministic context only when the target workflow declares those keys in `context_variables.yaml`.
- Use `entrypoints[]` entries with `transition: <id>` to enter a transition screen.
- Transition entry routes should use `meta.shellMode: "focused"` and transition
  UI should use `ui.shell_mode: "focused"` unless the transition intentionally
  needs normal product chrome.

## Transitions

Transitions are router decisions. They may render UI, but the declaration stays
semantic and workflow-agnostic.

```json
{
  "id": "app_type_selector",
  "transition_type": "user_choice_context",
  "ui": {
    "component": "AppTypeSelector",
    "mode": "screen",
    "shell_mode": "focused"
  },
  "options": [
    {
      "id": "greenfield_app",
      "route_to": "ValueEngine",
      "context_variables": { "app_type": "greenfield_app" }
    },
    {
      "id": "brownfield_app",
      "route_to": "ExistingAppDiscovery",
      "context_variables": { "app_type": "brownfield_app" }
    }
  ]
}
```

Supported target types:

- `route_to` can point to another transition id.
- `route_to` can point to a workflow id.
- The loader stamps `route_type` internally after validation.
- Transition UI emits `option_id`; it does not emit `route_to`.
- All user choice transitions declare `route_to` on each option.
- Use `user_choice_context` when the main point is deterministic context seeding, even if multiple options still route to the same next step.
- Use `user_choice_route` when the main point is branch routing across different targets.
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
factory_app/workflows/extended_orchestration/ui/index.js
```

The registry owns routing and context semantics (`transition_type`, single-route
`route_to`, `options[].route_to`, and `options[].context_variables`).

### `chat_session` Transition Type

`chat_session` launches a workflow in the current chat surface without mounting
a blocking overlay. It is the correct type for any post-generation review or
revision interaction where the user must be able to type freely in the chat.

**Declaration:**

```json
{
  "id": "app_review",
  "transition_type": "chat_session",
  "route_to": "AppReview"
}
```

**Rules:**

- `route_to` is required and must point to a registered workflow id.
- `chat_session` transitions must NOT declare a `ui` block — they never render
  an overlay component.
- `confirm_route`, `cancel_route`, `options`, `context_key`, and `routes` are
  not valid on `chat_session` transitions.

**Runtime behavior:**

1. The sequence auto-advances to the `chat_session` transition after the
   preceding workflow completes.
2. `TransitionScreen` receives the transition, detects `chat_session`, and
   immediately calls `onResolve(null)` without mounting any UI.
3. The shell calls `/api/transitions/resolve` and receives
   `resolution_type: "chat_session"` with `chat_id`, `workflow_id`, and the
   filtered `context_variables` from the active journey.
4. The shell switches the active chat session in-place to the launched
   workflow — no navigation away, no overlay, no blocked input.
5. The target workflow receives the accumulated journey `context_variables`
   filtered against its own `context_variables.yaml` declarations.

**When to use:**

Use `chat_session` when the interaction after transition must happen
conversationally — the user can type revision requests, the agent can respond,
and the flow can conclude through agent-authored completion signals rather than
a button-driven confirm/cancel overlay.

The canonical example is `app_review`, which launches the `AppReview` workflow
so the ReviewAgent can present a build summary artifact in chat and the user
can type revision requests or click Promote without a blocking screen.
Revision events emitted from this chat session must carry the reviewed
`artifact_key`, `artifact_version_id`, `source_surface=app_review`,
`lifecycle_state`, and staged `bundle_path` so the Refinement Engine
routes against the reviewed bundle rather than an ambient app workspace.
If the trigger response is `harness_decision`, the chat shell must feed it into
the shared pending harness decision UI before launching any downstream workflow.

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
  "affected_declarative_families": ["concept", "brand", "design_docs", "workflow_bundle", "app_bundle"],
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
- Use `entrypoints[].sequence` to declare the default journey entered by a route.
- Use `transitions[].options[].sequence` when a user choice must switch from the route's default journey into a different authored sequence.
- Use `affected_declarative_families` on sequences when the builder control
  plane needs to derive artifact invalidation impact from the selected route.
- Do not place a workflow in the same or earlier step as one of its required dependencies.
- If `B` depends on `A`, author the sequence as `A` in an earlier step and `B` in a later step.
- Do not list the same workflow more than once in a sequence.
- Entry UI belongs to `extension_registry.json -> entrypoints[]` via `transition`. The sequence may include the same transition as its first step so the journey contract remains fully ordered.

### Brownfield Context Refresh

`brownfield_discovery_refresh` is the canonical sequence for resolving a
Refinement Engine `ContextRefreshPlan` after stale brownfield context blocks a risky
refinement. It reruns `ExistingAppDiscovery` only.

`ContextRefreshPlan` creation is planning-only. It does not start this sequence
from stale-context policy evaluation, dry-run, or refinement routing. The
sequence starts only when an operator or caller explicitly invokes
`launch_context_refresh_plan`, which resolves the registered sequence and starts
`ExistingAppDiscovery` through the normal workflow launch path.

This sequence is non-mutating by shape:

- it contains no `AppGenerator` or `AgentGenerator` step
- it creates no staged patch and no generated overlay
- it does not promote or mutate source repositories
- it refreshes app-context artifact families and registers a new
  `AppContextVersion` through `ExistingAppDiscovery` persistence
- when source files are available, it persists `source_context_bundle` before
  `app_context_graph` so refinement tools can search and read exact code

It does not run `AppGenerator`, `AgentGenerator`, or `DesignDocs`.

After workflow completion, the Refinement Engine can call `complete_context_refresh`
to compare the previous context version with the new current
`AppContextVersion` and produce a `ContextRefreshResult`. That result records
whether stale context was actually resolved. It does not relaunch workflows,
mutate source repositories, or retry the blocked refinement automatically.

Brownfield generation/build sequences remain separate:

| Sequence | Purpose |
| --- | --- |
| `brownfield_app_adoption` | Capture source refs, index App Intelligence, run discovery chat, and pause for build-path selection. |
| `brownfield_overlay_generation` | Generate an overlay or bridge around selected existing-app surfaces. |
| `brownfield_module_generation` | Generate Mozaiks-owned modules from approved existing-app surfaces. |

### Brownfield App Intelligence UX

The existing-app intake path has two user-facing context surfaces:

1. `BrownfieldRepoInput` stays mounted after the user selects a repository and
   shows an extraction state while the workflow start request performs
   source-backed indexing.
2. `ExistingAppDiscovery` emits `AppIntelligenceOverviewCard` in chat after the
   `before_chat` preload completes and before the first agent message. The card
   renders the prompt-safe App Intelligence catalog: coverage, architecture
   surfaces, capability boundaries, integrations, data surfaces, risk hints, and
   agent context policy. It does not render raw source contents.

These surfaces are not AppPages. `BrownfieldRepoInput` is transition UI owned by
the build journey, and `AppIntelligenceOverviewCard` is workflow chat UI owned
by `ExistingAppDiscovery`. The Refinement Engine consumes the same App
Intelligence through checkpoint tools when the user later asks to revise or
extend the app.

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

Optional shell CTAs are declared separately in `shell.json`:

```json
{
  "header": {
    "actions": [
      {
        "id": "create-app",
        "intent": "create_app",
        "label": "Create App",
        "path": "/create"
      }
    ]
  }
}
```

If the CTA needs to change during an active workflow session, use semantic shell
action variants in `shell.json`; do not put route override logic in the
workflow registry.

`RouteRenderer` mounts `TransitionScreen`, and transition resolution calls
`/api/transitions/resolve`. If the result is another transition, the shell mounts
that transition. If the result is a workflow, the backend creates a chat session
and the shell navigates to chat.

The shell forwards `entrypoints[].sequence` as `journey_id` when it starts a
transition or workflow route. If a transition option declares its own
`sequence`, that option-level sequence overrides the inherited route journey for
the selected branch.

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
