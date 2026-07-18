---
name: control-plane-refinement-change
description: Review or implement a change to refinement routing, control_plane config, artifact routing, checkpoint re-entry, or factory_control_plane contributor guidance.
argument-hint: "[change summary or file path]"
---

Use this skill when a change touches control-plane or refinement behavior.

Typical triggers:

- `app/config/ai.json` startup settings in the canonical contract or the first-party repo path `factory_app/app/config/ai.json`
- `app/config/llm.yaml` or `factory_app/app/config/llm.yaml`
- `control_plane/config/control_plane.yaml` or `factory_app/control_plane/config/control_plane.yaml`
- checkpoint or re-entry behavior
- refinement classification and `patch|design|feature|core` routing
- `routing.artifacts[]` or artifact-family ownership
- `affected_declarative_families` on referenced `workflow_sequence` entries
- Studio refinement triggers or build sequence re-entry after a change request
- `factory_app/app/modules/factory_control_plane/`

Inspect first:

- `app/config/ai.json` in the canonical contract and the first-party repo path `factory_app/app/config/ai.json`
- `factory_app/app/config/ai.json`
- `factory_app/app/config/llm.yaml`
- `factory_app/control_plane/config/control_plane.yaml`
- `factory_app/workflows/extended_orchestration/extension_registry.json`
- `docs/architecture/workflows/refinement-control-plane.md`
- `docs/architecture/orchestration-and-decomposition.md`
- `docs/architecture/workflows/control-plane-harness-architecture.md`
- `factory_app/app/modules/factory_control_plane/module.yaml`
- `factory_app/app/modules/factory_control_plane/backend/handler.py`
- the narrowest matching tests before editing:
  - `tests/test_control_plane_loader.py`
  - `tests/test_refinement_router.py`
  - `tests/test_pack_config_paths.py`
  - `tests/test_build_lifecycle_hooks.py`

Current truth:

- refinement is checkpoint/control-plane re-entry, not a dedicated
  `RefinementWorkflow`
- `app/config/ai.json` owns runtime startup for `ask`, `chat`, and `workflows`
- `app/config/llm.yaml` owns control-plane runtime policy
- `control_plane/config/control_plane.yaml` owns declarative checkpoints and routing
- the first-party harness runtime lives in `mozaiksai/control_plane/`
- `factory_app/control_plane/` is the declarative first-party pack
- `factory_app/app/modules/factory_control_plane/` is a Studio identity stub only
- the control plane classifies change scope and routes to declared
  `workflow_sequence` re-entry points and checkpoints
- the referenced `workflow_sequence` owns downstream workflow order and
  `affected_declarative_families`

Keep these distinct:

- `workflow_sequence` / `workflow_sequences[]` = cross-workflow rebuild or revision route
- `transitions[]` = route-time user choice or deterministic context seed
- `entrypoints[]` = external route entry into a sequence or transition
- `transition_graph.yaml` = workflow-local AG2 agent routing
- `routing.artifacts[]` = artifact-kind ownership and change-class routing in `control_plane.yaml`
- `checkpoints[]` = control-plane decision points above workflows and above workflow-local handoffs

Routing rules:

- `control_plane.yaml` routes declare `workflow_sequence` only
- `workflow_sequence` ids must exist in
  `extended_orchestration/extension_registry.json`
- sequence-owned `affected_declarative_families` stay on the sequence, not in
  `control_plane.yaml`
- when present, `patch`, `design`, `feature`, and `core` classes must stay
  aligned across config, docs, and tests

Companion routing:

- Add `factory-build-workflow-change` as a companion skill when the change also alters `factory_app/workflows/extended_orchestration/extension_registry.json`, `workflow_sequence` composition, `transitions[]`, `entrypoints[]`, or other cross-workflow routing surfaces.

Do not:

- invent a dedicated `RefinementWorkflow`
- bypass build-sequence re-entry with ad hoc workflow routing
- confuse transitions with refinement routing or checkpoints with workflow-local handoffs
- route to undeclared `workflow_sequence` ids
- change artifact families or `affected_declarative_families` without updating tests
- put hosted product logic into OSS control-plane guidance or pack policy

Focused testing guidance:

- control-plane pack loading and route validation:
  - `python -m pytest tests/test_control_plane_loader.py -q`
- refinement routing and `affected_declarative_families` derivation:
  - `python -m pytest tests/test_refinement_router.py -q`
- workflow pack path and sequence resolution:
  - `python -m pytest tests/test_pack_config_paths.py -q`
- build-sequence lifecycle and journey-instance hook payloads:
  - `python -m pytest tests/test_build_lifecycle_hooks.py -q`

Final report requirements:

- Always include `OSS Change Impact`.
- Include `Control-Plane / Refinement Impact` when checkpoint routing, change classification, artifact routing, re-entry behavior, or sequence-based invalidation changed.

Return:

1. classification affected
2. artifact routing affected
3. workflow sequence affected
4. checkpoint/re-entry behavior
5. tests required/run

