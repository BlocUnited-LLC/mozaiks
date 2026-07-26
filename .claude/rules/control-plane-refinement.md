---
paths:
  - "docs/**/*.md"
  - ".claude/**/*.md"
  - "factory_app/refinement_harness/**"
  - "factory_app/app/config/ai.json"
  - "factory_app/app/config/refinement_policy.yaml"
  - "factory_app/app/modules/factory_control_plane/**"
  - "factory_app/workflows/extended_orchestration/**"
---

# Control-Plane Refinement Rules

Use these rules when changing refinement routing, harness configuration,
artifact routing, or checkpoint re-entry behavior.

## Current Truth

- Refinement today is checkpoint-driven re-entry driven by
  `app/config/ai.json` startup plus `app/config/refinement_policy.yaml`
  runtime policy and the selected `refinement_harness/config/harness.yaml`
  pack.
- Do not document or assume a dedicated `RefinementWorkflow` unless the runtime
  actually introduces one.
- The first-party harness runtime lives in `mozaiksai/control_plane/`.
- `factory_app/refinement_harness/` owns the first-party declarative pack,
  prompts, and tools.
- `factory_app/app/modules/factory_control_plane/` is a Studio identity stub,
  not the harness engine.

## Classification And Routing

- When the selected pack exposes `patch`, `design`, `feature`, and `core`
  routes, keep those classes aligned across docs, pack config, and tests.
- `refinement_harness/config/harness.yaml` routes declare `workflow_sequence`
  only.
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
- `transition_graph.yaml` = workflow-local agent routing
- `routing.artifacts[]` = artifact ownership and change-class routing
- `checkpoints[]` = Refinement Engine decision points above workflows

## Inspect These Anchors First

- `factory_app/app/config/ai.json`
- `factory_app/app/config/refinement_policy.yaml`
- `factory_app/refinement_harness/config/harness.yaml`
- `factory_app/workflows/extended_orchestration/extension_registry.json`
- `docs/architecture/workflows/refinement-engine.md`
- `docs/architecture/workflows/refinement-harness-architecture.md`
- `factory_app/app/modules/factory_control_plane/`

## Reporting

In reviews and final reports, include the `Control-Plane / Refinement Impact`
section from `.claude/rules/testing.md` when checkpoint routing, change
classification, artifact routing, or re-entry behavior changes.

