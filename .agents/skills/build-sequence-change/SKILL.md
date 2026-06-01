---
name: build-sequence-change
description: Review or implement a change to extension_registry workflow sequences, transitions, entrypoints, or cross-workflow build journey composition.
argument-hint: "[change summary or file path]"
---

Use this skill when a change touches build journey composition.

Inspect first:

- `factory_app/workflows/extended_orchestration/extension_registry.json`
- `docs/architecture/workflows/workflow-routing-transitions.md`
- `docs/architecture/orchestration-and-decomposition.md`
- `docs/architecture/workflows/control-plane-harness-architecture.md` when
  sequence ids are referenced by control-plane routes
- the `orchestrator.yaml` and `handoffs.yaml` files of each affected workflow

Keep these distinct:

- `workflow_sequence` / `workflow_sequences[]` = cross-workflow build or revision journey
- `transitions[]` = user choice or deterministic context seed
- `handoffs.yaml` = agent routing inside one workflow
- `entrypoints[]` = external route entry into a sequence or transition

Current build truth:

- build is `workflow_sequence`-driven
- `ValueEngine` owns concept and value decomposition
- `ThemeCapture` owns brand and theme capture
- `DesignDocs` owns design intent
- `AgentGenerator` owns workflow bundle generation
- `AppGenerator` owns final app-bundle generation
- `ExistingAppDiscovery` is brownfield discovery, not the default greenfield build path

If a `workflow_sequence` id changes, update `control_plane.yaml`, docs, and
tests that reference it.

Return:

1. workflow_sequence affected
2. workflows affected
3. transitions affected
4. entrypoints affected
5. downstream artifacts affected
6. tests required/run
7. rollback risk
