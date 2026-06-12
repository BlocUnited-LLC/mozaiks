---
paths:
  - "ARCHITECTURE.md"
  - "docs/**/*.md"
  - ".claude/**/*.md"
  - "factory_app/workflows/**"
  - "factory_app/build_context/**"
---

# Factory Build Workflow Rules

Use these rules when changing build journeys, workflow sequencing, or builder
workflow ownership.

## Build Truth

- Build is `workflow_sequence`-driven through
  `factory_app/workflows/extended_orchestration/extension_registry.json`.
- `workflow_sequences[]` declares the ordered cross-workflow build or revision
  journey.
- `AppGenerator` is the final app-bundle workflow in several sequences. It is
  not the whole build system.
- `ValueEngine` owns concept and value decomposition.
- `ThemeCapture` owns visual identity capture and theme authority.
- `DesignDocs` owns frontend, backend, database, and UI-schema design intent.
- `AgentGenerator` owns workflow bundle generation and workflow-side contracts.
- `ExistingAppDiscovery` belongs to the brownfield flow, not the default
  greenfield build path.

## Routing Vocabulary

Keep these mechanisms distinct:

- `workflow_sequence` / `workflow_sequences[]` = cross-workflow build or
  revision journey
- `transition_graph.yaml` = agent routing inside one workflow
- `transitions[]` = user choice or deterministic context seeding between steps
- `entrypoints[]` = external route entry into a sequence or transition

Do not describe one of these as another.

## Change Rules

- When a sequence changes, inspect the owning workflow list, dependencies,
  entrypoints, transitions, and affected declarative families together.
- If a `workflow_sequence` id changes, update any `control_plane.yaml` routes,
  docs, and tests that reference it.
- Keep workflow responsibilities explicit. Full-build descriptions should name
  the relevant sequence and contributing workflows, not collapse everything into
  `AppGenerator`.
- Brownfield adoption changes must inspect `ExistingAppDiscovery` plus the
  `brownfield_app_adoption` sequence.

## Build Input Structure

- Build contexts are named directories with one `context.yaml` registry.
- `context.yaml` declares explicit `assets[]`; folder names are organization,
  not policy.
- Factory prompt catalogs live under `factory_app/build_context/{context_name}/`
  and are declared as `assets[]` with `kind: catalog`.
- Workflow runtime YAML lives under `factory_app/workflows/{WorkflowName}/`.
- Hooks resolve factory build-context paths through
  `factory_app.workflows._shared.hook_utils.workflow_context_path()`.
- Do not recreate `factory_app/workflows/_shared/catalogs/`.
- Do not create a one-off catalog family folder unless there is a real
  organization need and the file is still declared in `assets[]`.
- `context_variables.yaml` is runtime/session state. Do not store large static
  prompt catalogs in context variables.
- Reusable OSS build packs are named build contexts. Add them only when
  `context.yaml` declares a `pack:` descriptor and real assets such as
  `kind: contract` and `kind: templates`, plus validation and tests.
- Workspace build contexts use the same named-context shape under
  workspace-root `build_context/{context_name}/`.

## Reporting

In reviews and final reports, include the `Build Workflow Sequence Impact`
section from `.claude/rules/testing.md` when a sequence, transition,
entrypoint, or cross-workflow build journey changes.


