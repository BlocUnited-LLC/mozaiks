---
name: factory-build-workflow-change
description: Review or implement a change to the factory build workflow system, including workflow sequences, transitions, entrypoints, AppGenerator, AgentGenerator, ExistingAppDiscovery, artifact routing, and build lifecycle integration.
argument-hint: "[change summary or file path]"
---

Use this skill when a change touches the factory build workflow system.

Typical triggers:

- `factory_app/workflows/extended_orchestration/extension_registry.json`
- `workflow_sequences[]`, `transitions[]`, or `entrypoints[]`
- build journey sequencing or branch routing
- `factory_app/workflows/AppGenerator/**`
- `factory_app/workflows/AgentGenerator/**`
- `factory_app/workflows/ExistingAppDiscovery/**`
- build lifecycle hooks in `factory_app/workflows/_shared/platform/build_lifecycle.py`
- artifact routing, generated artifact ownership, or workflow integration contracts
- shared workflow tools, hooks, or cross-workflow build glue
- build control-plane or refinement integration that references `workflow_sequence`

Inspect first:

- `ARCHITECTURE.md`
- `AGENTS.md`
- `CLAUDE.md`
- `.claude/rules/factory-build-workflows.md`
- `.claude/rules/build-refinement-truth.md`
- `.claude/rules/testing.md`
- `factory_app/workflows/extended_orchestration/extension_registry.json`
- `docs/architecture/workflows/workflow-routing-transitions.md`
- `docs/architecture/orchestration-and-decomposition.md`
- `docs/architecture/workflows/refinement-control-plane.md`
- the full affected workflow directory, especially:
  - `orchestrator.yaml`
  - `handoffs.yaml`
  - `agents.yaml`
  - `structured_outputs.yaml`
  - `tools.yaml`
  - `context_variables.yaml`
  - `hooks.yaml`
- the relevant shared workflow surfaces when build lifecycle or shared tools change:
  - `factory_app/workflows/_shared/platform/build_lifecycle.py`
  - any affected `factory_app/workflows/_shared/**` helper
- generated artifact contracts when AppGenerator or AgentGenerator is affected:
  - `factory_app/workflows/AppGenerator/tools/file_contracts.yaml`
  - relevant workflow-converter or materialization contracts
- the narrowest matching tests before editing:
  - sequence, transition, and entrypoint routing: `tests/test_pack_schema_models.py`, `tests/test_pack_config_paths.py`, `tests/test_existing_app_discovery_contracts.py`
  - build lifecycle hooks: `tests/test_build_lifecycle_hooks.py`
  - ExistingAppDiscovery: `tests/test_existing_app_discovery_contracts.py`, `tests/test_existing_app_discovery_native_migration.py`
  - AppGenerator: `tests/test_appgenerator_canonical_generation.py`, `tests/test_appgenerator_module_contracts.py`, `tests/test_appgenerator_persistence_alignment.py`
  - AgentGenerator: `tests/test_agentgenerator_workflow_converter.py`, `tests/test_agentgenerator_tool_planning.py`, `tests/test_agentgenerator_ui_quality_gate.py`
  - artifact routing and control-plane linkage: `tests/test_control_plane_loader.py`, `tests/test_control_plane_tools.py`, `tests/test_hook_file_contract_context.py`, `tests/test_hook_domain_catalog_context.py`, `tests/test_build_lifecycle_hooks.py`

Build sequence truth:

- build is `workflow_sequence`-driven through `factory_app/workflows/extended_orchestration/extension_registry.json`
- `AppGenerator` is one workflow in the build sequence, not the whole build system
- `AgentGenerator` is one workflow in the build sequence, not the whole build system
- `ExistingAppDiscovery` belongs to the brownfield or existing-app adoption sequence
- `workflow_sequence`, `transitions[]`, `entrypoints[]`, and workflow-local `handoffs.yaml` are different mechanisms with different owners
- `workflow_sequence` auto-advance must not be used as a human review or HITL boundary
- transition options may switch sequences through `options[].sequence`; that is different from workflow-local agent routing

Boundary rules:

- Do not change runtime or platform behavior from this skill unless the task explicitly expands into those layers.
- Do not make `AppGenerator` own the entire build process.
- Do not add private hosted-product logic, secrets, or proprietary workflow policy to OSS build workflows.
- Do not conflate greenfield build journeys with brownfield or existing-app adoption journeys.
- Do not add `transitions[]` when workflow-local `handoffs.yaml` is sufficient.
- Do not add `workflow_sequence` steps for operator-review or HITL checkpoints that should remain explicit surfaced review boundaries.
- Do not route to workflows that are not declared in the registry.
- Do not modify generated app file contracts casually; inspect downstream AppGenerator or AgentGenerator contract tests first.
- Do not describe transition routing, workflow sequencing, refinement routing, and MFJ as the same layer.

Common change types:

1. Adding or changing `workflow_sequence` steps:
   - inspect `extension_registry.json`, the affected workflow dependencies, and the matching sequence tests together
   - confirm the sequence order respects declared dependencies and artifact-family ownership
   - check whether `control_plane.yaml` routes or refinement docs reference the sequence id
2. Adding or changing transitions:
   - inspect `extension_registry.json`, transition UI docs, and the target workflow `context_variables.yaml`
   - use transitions for route-time user choice or deterministic context seeding, not for workflow-local agent routing
3. Adding or changing entrypoints:
   - inspect `entrypoints[]`, the target transition, the target sequence, and any shell intent docs or tests
   - keep route entry distinct from journey sequencing and workflow-local handoffs
4. Changing AppGenerator:
   - inspect `factory_app/workflows/AppGenerator/` contracts, hooks, and `tools/file_contracts.yaml`
   - treat AppGenerator as the app-bundle workflow inside the broader build system, not the owner of the entire journey
5. Changing AgentGenerator:
   - inspect `factory_app/workflows/AgentGenerator/` plus workflow-converter tests and generated workflow artifact expectations
   - keep workflow bundles and app bundles as separate artifact families
6. Changing ExistingAppDiscovery:
   - inspect `factory_app/workflows/ExistingAppDiscovery/` and brownfield sequence routing in `extension_registry.json`
   - keep brownfield adoption as a distinct journey with its own routing and artifact expectations
7. Changing artifact routing, refinement, or control-plane linkage:
   - inspect `docs/architecture/workflows/refinement-control-plane.md`, `factory_app/control_plane/config/control_plane.yaml`, and `tests/test_control_plane_loader.py`
   - routes use `workflow_sequence`; do not duplicate downstream workflow lists when the sequence is the source of truth
8. Changing build lifecycle hooks:
   - inspect `factory_app/workflows/_shared/platform/build_lifecycle.py` and `tests/test_build_lifecycle_hooks.py`
   - preserve journey-aware payloads, runtime hook kwargs, and sequence-position semantics
9. Changing shared workflow tools or hooks:
   - inspect the affected workflow `hooks.yaml` or shared tool file plus the nearest hook or workflow-contract tests
   - keep shared helpers generic; do not bury product-specific policy in shared builder tooling

Focused testing guidance:

- Run the narrowest matching test slice first.
- Sequence, transition, and entrypoint changes:
  - `python -m pytest tests/test_pack_schema_models.py tests/test_pack_config_paths.py tests/test_existing_app_discovery_contracts.py -q`
- ExistingAppDiscovery or brownfield changes:
  - `python -m pytest tests/test_existing_app_discovery_contracts.py tests/test_existing_app_discovery_native_migration.py -q`
- AppGenerator contract or artifact changes:
  - `python -m pytest tests/test_appgenerator_module_contracts.py tests/test_appgenerator_canonical_generation.py tests/test_appgenerator_persistence_alignment.py -q`
- AgentGenerator workflow-bundle changes:
  - `python -m pytest tests/test_agentgenerator_workflow_converter.py tests/test_agentgenerator_tool_planning.py tests/test_agentgenerator_ui_quality_gate.py -q`
- Control-plane or artifact-routing linkage:
  - `python -m pytest tests/test_control_plane_loader.py tests/test_control_plane_tools.py tests/test_hook_file_contract_context.py tests/test_hook_domain_catalog_context.py -q`
- Build lifecycle hooks:
  - `python -m pytest tests/test_build_lifecycle_hooks.py -q`
- Generated artifact or file-contract changes:
  - add the nearest AppGenerator or AgentGenerator contract slice rather than defaulting to a broad repo run

Final report requirements:

- Always include `OSS Change Impact`.
- Include `Build Workflow Sequence Impact` whenever sequence, transition, entrypoint, build lifecycle routing, or cross-workflow factory composition changed.

## Build Workflow Sequence Impact

- workflow_sequence affected
- workflows affected
- transitions affected
- entrypoints affected
- downstream artifacts affected
- tests run
- rollback risk

Return:

1. workflow_sequence or routing surface affected
2. workflows affected
3. artifacts or contracts affected
4. control-plane or refinement linkage impact
5. tests required or run
6. rollback or contract drift risk
