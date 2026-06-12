---
name: existing-app-discovery-change
description: Review or implement a change to ExistingAppDiscovery, brownfield_app_adoption routing, preload discovery, adoption classification, module decomposition outputs, or brownfield artifact saving.
argument-hint: "[change summary or file path]"
---

Use this skill when a change touches the brownfield or existing-app discovery flow.

Typical triggers:

- `factory_app/workflows/ExistingAppDiscovery/**`
- `brownfield_app_adoption` in `factory_app/workflows/extended_orchestration/extension_registry.json`
- existing app preload or discovery tools
- adoption levels or adoption rationale
- `embed`, `bridge`, `ecosystem`, or `gradual_modernization` decisions
- module decomposition outputs for brownfield adoption
- storage, connector, auth, or security detection
- brownfield artifact saving or downstream discovery artifact consumption
- ExistingAppDiscovery workflow-local handoffs or brownfield routing into later factory workflow contracts

Inspect first:

- `factory_app/workflows/extended_orchestration/extension_registry.json`
- `docs/architecture/workflows/workflow-routing-transitions.md`
- `docs/architecture/orchestration-and-decomposition.md`
- `factory_app/workflows/ExistingAppDiscovery/agents.yaml`
- `factory_app/workflows/ExistingAppDiscovery/structured_outputs.yaml`
- `factory_app/workflows/ExistingAppDiscovery/transition_graph.yaml`
- `factory_app/workflows/ExistingAppDiscovery/context_variables.yaml`
- `factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py`
- `factory_app/workflows/ExistingAppDiscovery/tools/save_existing_app_artifacts.py`
- `tests/test_existing_app_discovery_contracts.py`
- the narrowest matching brownfield routing tests before editing:
  - `tests/test_existing_app_discovery_gradual_modernization.py`
  - `tests/test_session_router.py`
  - `tests/test_session_launcher.py`
  - `tests/test_pack_schema_models.py`

Core truth:

- `ExistingAppDiscovery` belongs to the brownfield or existing-app adoption flow.
- It is not the default greenfield build path.
- It is not `AppGenerator`.
- It should analyze an existing app and produce structured adoption, discovery, and decomposition artifacts.
- It must not copy existing-app code directly into generated apps without a conscious adoption strategy.
- Separate docs and stated intent from code truth when evaluating an external app.
- Code truth wins over docs, screenshots, or product summaries when they conflict.
- `embed`, `bridge`, `ecosystem`, and `gradual_modernization` are current workflow-local adoption labels. They are discovery evidence, not canonical AppContext artifact kinds.
- `brownfield_app_adoption` currently routes into `ExistingAppDiscovery`; it does not continue through the default greenfield build sequence unless the authored sequence contract changes.
- Downstream factory workflow consumption should happen through saved artifacts and explicit contracts, not by pretending discovery is already `AppGenerator`.

**AppContext target direction:**
`ExistingAppDiscovery` currently emits workflow-local outputs including `ExistingProductSpec`,
`CapabilitySpec[]`, `AgentAugmentationPlan`, and optionally `module_decomposition_plan`. These are
retired evidence outputs — they are not canonical `AppContextVersion` artifact kinds and must not
become control-plane source of truth. The target canonical substrate for both greenfield and
brownfield apps is `AppContextVersion`. See
`docs/architecture/foundations/app-context-and-brownfield-adoption.md`.

Boundary rules:

- Do not make `ExistingAppDiscovery` the greenfield build path.
- Do not make `AppGenerator` responsible for brownfield discovery, preload scanning, or adoption classification.
- Do not treat docs as the source of truth when analyzing an external app.
- Code truth wins.
- Do not copy provider-specific or proprietary implementation into OSS fixtures, examples, or tests.
- Use neutral fixture apps in tests.
- Do not copy existing-app code directly into generated apps as an implicit migration strategy.
- When old in-flight outputs use `native_migration` adoption level, treat that as retired historical discovery evidence feeding the `AdoptionPlan` / `AppContextVersion` contracts — not as a canonical artifact kind to preserve or extend.
- Keep `workflow_sequence`, transition routing, and workflow-local `transition_graph.yaml` distinct.
- Do not assume later factory workflows are part of the brownfield path unless `extension_registry.json` and the saved artifact contracts explicitly say so.

Common change types:

1. Updating preload scanners:
   - inspect `preload_discovery_context.py`, `context_variables.yaml`, and the nearest preload tests together
   - keep preload deterministic; it gathers evidence, it does not make the final adoption decision by itself
2. Updating tech-stack detection:
   - anchor the change in repo, API, or runtime evidence
   - keep detection provider-neutral and avoid prose-only inference
3. Updating storage detection:
   - preserve the meaning of `storage_pattern` and `storage_migration_required`
   - inspect `tests/test_existing_app_discovery_gradual_modernization.py`
4. Updating connector detection:
   - keep connector classification generic and OSS-safe
   - do not turn discovery fixtures into provider-specific implementation examples
5. Updating auth or security detection:
   - distinguish current auth evidence from recommended migration hardening
   - keep detection grounded in repo, runtime, or OpenAPI evidence
6. Updating adoption-level classification:
   - keep `embed`, `bridge`, `ecosystem`, and `gradual_modernization` separate
   - do not collapse brownfield adoption into a default rebuild recommendation
7. Updating gradual modernization outputs:
   - inspect `structured_outputs.yaml`, `transition_graph.yaml`, and the decomposition-related tests
   - `module_decomposition_plan` is workflow-local evidence for `ecosystem` and `gradual_modernization` adoption levels
   - treat it as internal evidence feeding `AdoptionPlan` / `AppContextVersion` contracts, not as a canonical artifact kind to extend
8. Updating artifact saving:
   - inspect `save_existing_app_artifacts.py` and the artifact contract tests
   - preserve canonical saved fields and artifact-based downstream handoff expectations
9. Updating brownfield workflow sequence or handoffs:
   - inspect `extension_registry.json`, `transition_graph.yaml`, and the brownfield sequence tests together
   - keep route entry, workflow-local agent routing, and downstream artifact consumption as separate layers
10. Updating tests or fixtures:
   - use neutral host apps and OSS-safe examples
   - keep fixture evidence realistic without copying private product behavior or proprietary implementations

Focused testing guidance:

- ExistingAppDiscovery contract and artifact tests:
  - `python -m pytest tests/test_existing_app_discovery_contracts.py tests/test_existing_app_discovery_gradual_modernization.py -q`
- preload scanner, storage, connector, and adoption-classification changes:
  - `python -m pytest tests/test_existing_app_discovery_gradual_modernization.py -q`
- brownfield sequence and route binding changes:
  - `python -m pytest tests/test_session_router.py tests/test_session_launcher.py tests/test_pack_schema_models.py -q`
- docs or contributor-guidance changes for this skill:
  - `python -m pytest tests/test_existing_app_discovery_skill.py tests/test_contributor_guidance_framing.py -q`

Final report requirements:

- Always include `OSS Change Impact`.
- Include `Build Workflow Sequence Impact` when `extension_registry.json`, `workflow_sequence`, `transitions[]`, `entrypoints[]`, or cross-workflow brownfield routing changed.
- Include `Brownfield Discovery Impact` when preload scanning, detection, adoption classification, decomposition outputs, artifact saving, or workflow-local discovery routing changed.

## Brownfield Discovery Impact

- Existing workflow affected
- Adoption levels affected
- Detectors affected
- Artifacts affected
- Tests run
- Compatibility risk

Return:

1. brownfield workflow surface affected
2. adoption levels or classifiers affected
3. detectors or preload surfaces affected
4. artifacts or downstream handoff impact
5. tests required or run
6. adoption risk


