---
paths:
  - "docs/**/*.md"
  - ".claude/**/*.md"
  - "factory_app/control_plane/**"
  - "factory_app/app/config/ai.json"
  - "factory_app/app/modules/factory_control_plane/**"
  - "factory_app/workflows/extended_orchestration/**"
---

# Control-Plane Refinement Rules

Use these rules when changing refinement routing, harness configuration,
artifact routing, or checkpoint re-entry behavior.

## Current Truth

- Refinement today is checkpoint/control-plane re-entry driven by
  `app/config/ai.json` plus the selected `control_plane.yaml` pack.
- Do not document or assume a dedicated `RefinementWorkflow` unless the runtime
  actually introduces one.
- The first-party harness runtime lives in `mozaiksai/control_plane/`.
- `factory_app/control_plane/` owns the first-party declarative pack, prompts,
  and tools.
- `factory_app/app/modules/factory_control_plane/` is a Studio identity stub,
  not the harness engine.

## Classification And Routing

- When the selected pack exposes `patch`, `design`, `feature`, and `core`
  routes, keep those classes aligned across docs, pack config, and tests.
- `control_plane.yaml` routes declare `workflow_sequence` only.
- The referenced `workflow_sequence` in
  `extended_orchestration/extension_registry.json` owns downstream workflow
  ordering and `affected_declarative_families`.
- Do not duplicate downstream workflow lists or artifact-family impact inside
  `control_plane.yaml`.
- Artifact routing belongs under `routing.artifacts[]` by `artifact_kind`.

Keep these mechanisms distinct:

- `workflow_sequence` / `workflow_sequences[]` = cross-workflow re-entry or rebuild path
- `transitions[]` = route-time user choice or deterministic context seed
- `entrypoints[]` = external route entry into a sequence or transition
- `handoffs.yaml` = workflow-local agent routing
- `routing.artifacts[]` = artifact ownership and change-class routing
- `checkpoints[]` = control-plane decision points above workflows

## Inspect These Anchors First

- `factory_app/app/config/ai.json`
- `factory_app/control_plane/config/control_plane.yaml`
- `factory_app/workflows/extended_orchestration/extension_registry.json`
- `docs/architecture/workflows/refinement-control-plane.md`
- `docs/architecture/workflows/control-plane-harness-architecture.md`
- `factory_app/app/modules/factory_control_plane/`

## Reporting

In reviews and final reports, include the `Control-Plane / Refinement Impact`
section from `.claude/rules/testing.md` when checkpoint routing, change
classification, artifact routing, or re-entry behavior changes.