---
name: appgenerator-change
description: Review or implement a change to AppGenerator prompts, AppBuildPlan contracts, file contracts, assembly behavior, generated UI quality gates, or app-bundle generation guidance.
argument-hint: "[change summary or file path]"
---

Use this skill when a change touches AppGenerator specifically.

Typical triggers:

- `factory_app/workflows/AppGenerator/**`
- AppGenerator agents or prompts
- `AppBuildPlan` shape or validation
- `structured_outputs.yaml`
- `factory_app/build_context/AppGenerator/file_contracts.yaml`
- `factory_app/build_context/AppGenerator/domain_catalogs.yaml`
- `factory_app/build_context/AppGenerator/module_archetypes.yaml`
- `tools/app_build_plan.py`
- `tools/assemble_app_tasks.py`
- shared generated UI contract checks used by AppGenerator
- module, page, or data contract generation behavior
- managed-capability or external-adapter planning
- app-owned facade module patterns
- AppGenerator-specific tests or fixtures

Inspect first:

- `factory_app/workflows/AppGenerator/agents.yaml`
- `factory_app/workflows/AppGenerator/structured_outputs.yaml`
- `factory_app/workflows/AppGenerator/middleware.yaml`
- `factory_app/workflows/AppGenerator/transition_graph.yaml`
- `factory_app/workflows/AppGenerator/context_variables.yaml`
- `factory_app/build_context/AppGenerator/file_contracts.yaml`
- `factory_app/build_context/AppGenerator/domain_catalogs.yaml`
- `factory_app/build_context/AppGenerator/module_archetypes.yaml`
- `factory_app/workflows/AppGenerator/tools/app_build_plan.py`
- `factory_app/workflows/AppGenerator/tools/assemble_app_tasks.py`
- `factory_app/workflows/_shared/generated_ui_contract.py`

Catalog placement rule:

- AppGenerator prompt catalogs live only under `factory_app/build_context/AppGenerator/`.
- AppGenerator runtime workflow files live under `factory_app/workflows/AppGenerator/`.
- Hooks resolve factory build-context paths through `factory_app.workflows._shared.hook_utils.workflow_context_path()`.
- Do not recreate `factory_app/workflows/_shared/catalogs/`.
- Do not add static prompt catalog values to `context_variables.yaml`; context variables are runtime/session state.
- the narrowest relevant AppGenerator tests before editing:
  - `tests/test_appgenerator_module_contracts.py`
  - `tests/test_appgenerator_canonical_generation.py`
  - `tests/test_appgenerator_persistence_alignment.py`
  - `tests/test_appgenerator_validate_wiring.py`
  - `tests/test_appgenerator_ui_quality_gate.py`
  - `tests/test_appgenerator_component_drift_guard.py`
  - `tests/test_appgenerator_managed_capability_smoke.py`
  - `tests/test_appgenerator_generated_page_binding.py`
- inspect upstream build-sequence context only when the AppGenerator change depends on upstream inputs or assumptions from `ValueEngine`, `ThemeCapture`, `DesignDocs`, or `AgentGenerator`

Core truth:

- AppGenerator is the final app-bundle generation workflow inside the broader factory `workflow_sequence`.
- It consumes upstream outputs from earlier build workflows.
- It does not own product strategy, concept formation, brand discovery, or workflow generation.
- It emits canonical app workspace artifacts.
- It must respect the canonical app structure and the platform's current runtime contracts.
- It must respect `factory_app/build_context/AppGenerator/file_contracts.yaml`.
- It must not invent runtime contracts unsupported by the platform.
- It must not generate provider-owned managed capability internals.
- It should keep persistent app UI schema-first unless the bounded custom route contract is explicitly required.

Boundary rules:

- Do not treat AppGenerator as the whole build system.
- Do not change `workflow_sequence` composition from this skill; use `factory-build-workflow-change` when the change widens into `extension_registry.json`, transitions, entrypoints, or cross-workflow build composition.
- Do not generate `backend/models.py` or `backend/models/*.py`.
- Do not generate `contracts/subscriptions.yaml` as the canonical reaction contract.
- Do not generate flat root manifests when the canonical contract uses `contracts/`.
- Do not generate `app/capability_packs/`.
- Do not generate `transport.py` or duplicate runtime transport infrastructure.
- Do not bind pages directly to managed-capability internals; use the app-owned facade module pattern.
- Do not hardcode provider-specific or private product examples into OSS guidance, tests, or fixtures.
- Do not assume `ctx.db`; use the canonical persistence model and `ctx.persistence.collection(module_id, entity_name)`.
- Do not generate local visual primitive clones or raw persistent-page React when the shipped schema and primitive contracts can represent the surface.

Common change types:

1. AppBuildPlan validation changes:
   - inspect `app_build_plan.py`, `structured_outputs.yaml`, and the nearest build-plan tests together
   - preserve the boundary between plan validation, task ownership, and runtime behavior
2. File contract changes:
   - inspect `factory_app/build_context/AppGenerator/file_contracts.yaml` plus the nearest canonical generation and helper-contract tests
   - keep owned paths, output families, and hard constraints aligned with current runtime and loader truth
3. Module generation changes:
   - inspect `structured_outputs.yaml`, `factory_app/build_context/AppGenerator/file_contracts.yaml`, `factory_app/build_context/AppGenerator/module_archetypes.yaml`, and module contract tests together
   - preserve `module.yaml` plus `contracts/` ownership and the canonical backend layer split
4. Page or UI generation changes:
   - inspect `agents.yaml`, `structured_outputs.yaml`, `assemble_app_tasks.py`, and `factory_app/workflows/_shared/generated_ui_contract.py`
   - keep persistent pages declarative by default and custom routes bounded by the typed contract
5. Data contract generation changes:
   - inspect `structured_outputs.yaml`, `factory_app/build_context/AppGenerator/file_contracts.yaml`, and persistence tests together
   - keep `data/contract.json` and `data/migrations/{migration_id}.json` as the canonical output family
6. Managed-capability or external-adapter generation changes:
   - inspect `factory_app/build_context/AppGenerator/file_contracts.yaml`, managed-capabilities rules, and managed-capability smoke tests together
   - preserve the app-owned facade module pattern and thin adapter boundary
7. Assembly or template behavior:
   - inspect `assemble_app_tasks.py`, `assembly_phase.py`, and the nearest assembly or validation tests
   - keep assembly as artifact composition, not a place to invent new contracts
8. Generated UI quality gates:
   - inspect `middleware.yaml`, `tools.yaml`, `factory_app/workflows/_shared/generated_ui_contract.py`, and UI quality tests together
   - keep shared UI quality standards aligned with the frontend rules
9. Prompt or hook changes:
   - inspect `agents.yaml`, `middleware.yaml`, the injected contract contexts, and prompt-drift tests together
   - do not weaken canonical file, schema, or boundary guidance in prompts
10. Test fixture updates:
   - update the narrowest fixture or contract slice that actually changed
   - keep examples provider-neutral and OSS-safe

Focused testing guidance:

- AppBuildPlan validation and wiring:
  - `python -m pytest tests/test_appgenerator_validate_wiring.py tests/test_appgenerator_managed_capability_smoke.py -q`
- AppGenerator canonical generation and module contract guidance:
  - `python -m pytest tests/test_appgenerator_canonical_generation.py tests/test_appgenerator_module_contracts.py -q`
- generated UI contract and UI quality gates:
  - `python -m pytest tests/test_appgenerator_ui_quality_gate.py tests/test_appgenerator_component_drift_guard.py -q`
- file contract and backend helper rules:
  - `python -m pytest tests/test_appgenerator_backend_helper_contracts.py -q`
- data contract and persistence alignment:
  - `python -m pytest tests/test_appgenerator_persistence_alignment.py tests/test_appgenerator_persistent_module_generation.py -q`
- managed-capability facade and page binding checks:
  - `python -m pytest tests/test_appgenerator_managed_capability_smoke.py tests/test_appgenerator_generated_page_binding.py tests/test_appgenerator_managed_facade_binding.py -q`
- docs or contributor-guidance changes for this skill:
  - `python -m pytest tests/test_appgenerator_change_skill.py tests/test_contributor_quickstart.py tests/test_claude_guidance_operating_system.py -q`

Final report requirements:

- Always include `OSS Change Impact`.
- Always include `AppGenerator Workflow Impact`.
- Include `Module Contract Impact` when module contract files, generated module backend structure, or module loader assumptions changed.
- Include `Managed Capability Boundary Check` when managed-capability classification, facade routing, or adapter clients changed.
- Include `Build Workflow Sequence Impact` when AppGenerator assumptions changed because sequence composition, transitions, entrypoints, or upstream build ownership changed.

## AppGenerator Workflow Impact

- AppGenerator component changed
- upstream build sequence assumptions
- generated artifacts affected
- runtime/platform contracts affected
- tests run
- contract drift risk

Return:

1. AppGenerator component affected
2. upstream sequence or input assumptions affected
3. generated artifacts or contracts affected
4. runtime/platform contract impact
5. tests required or run
6. contract drift risk






