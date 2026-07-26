---
paths:
  - "ARCHITECTURE.md"
  - "docs/**/*.md"
  - ".claude/**/*.md"
---

# Build And Refinement Truth Rules

Use these rules when describing the builder lifecycle, workflow routing, or
refinement behavior.

- Build is workflow-sequence-driven through
  `factory_app/workflows/extended_orchestration/extension_registry.json`.
- `AppGenerator` and `AgentGenerator` are individual workflows inside the build
  sequence, not the build system itself.
- `ExistingAppDiscovery` is the brownfield/existing-app adoption workflow path,
  not the default greenfield build flow. It produces onboarding artifacts
  (`ExistingProductSpec`, `CapabilitySpec[]`, `AgentAugmentationPlan`,
  `module_decomposition_plan`). These are retired evidence outputs, not canonical
  `AppContextVersion` artifact kinds. The target canonical substrate for both
  greenfield and brownfield apps is `AppContextVersion`. See
  `docs/architecture/foundations/app-context-and-brownfield-adoption.md`.
- Keep these mechanisms distinct: `transition_graph.yaml` for workflow-local agent
  routing, `workflow_sequences[]` for cross-workflow build/revision sequencing,
  `transitions[]` for routed entry and user choice flows, and `task_batches.yaml`
  for bounded workflow-local parallel task work.
- Current refinement is checkpoint-driven re-entry driven by
  `app/config/ai.json` startup, `app/config/refinement_policy.yaml` runtime
  policy, and the selected `refinement_harness/config/harness.yaml` pack. Do
  not claim a dedicated `RefinementWorkflow` unless the runtime introduces one.
- If module event/reaction docs differ from the current implementation, direct
  contributors to inspect the module loader and tests and follow the implemented
  runtime truth for that change.


