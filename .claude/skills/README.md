# Skills Index

Use this index to choose the closest skill before nontrivial work.

## Active Task Map

| Change type | Use this skill | Notes |
| --- | --- | --- |
| Runtime/platform change | `runtime-change` | Use `runtime-architecture-review` when you only need a pre-change or post-change boundary review. |
| Auth change | `runtime-change` | Auth is runtime/platform substrate unless the task is purely docs or tests. |
| Build sequence / factory workflow changes | `factory-build-workflow-change` | Use `build-sequence-change` when you only need a narrow sequence or journey-composition review. |
| AppGenerator-specific change | `appgenerator-change` | Use `appgenerator-change` for AppGenerator-local changes. Add `factory-build-workflow-change` only when the change also affects `workflow_sequence`, sequence design, `transitions[]`, `entrypoints[]`, or cross-workflow build ownership. |
| AgentGenerator-specific change | `agentgenerator-change` | Use `agentgenerator-change` for AgentGenerator-local changes. Add `factory-build-workflow-change` only when the change also affects `workflow_sequence`, sequence design, `transitions[]`, `entrypoints[]`, or cross-workflow build ownership. |
| ExistingAppDiscovery change | `existing-app-discovery-change` | Use `existing-app-discovery-change` for ExistingAppDiscovery-local changes. Also use for brownfield discovery, adoption classification, or AppContext onboarding changes. Add `factory-build-workflow-change` only when the change also affects `extension_registry.json`, `workflow_sequence`, `transitions[]`, `entrypoints[]`, or cross-workflow sequence composition. |
| Control-plane / refinement / harness routing | `control-plane-refinement-change` | Covers `app/config/ai.json` startup, `app/config/llm.yaml`, `control_plane/config/control_plane.yaml`, artifact routing, and checkpoint re-entry. Add `factory-build-workflow-change` too when `workflow_sequence` composition or `extension_registry.json` routing changes. |
| Module contract change | `add-module` | Use `runtime-change` when module loader/executor/runtime behavior changes. Use `appgenerator-change` when generated module output changes. |
| Add a deterministic backend module | `add-module` | Use for authoring or scaffolding a new module in an app workspace. |
| Page or frontend change | `add-page` | Use for AppPageSchema, route-manifest, or custom route work. |
| Admin UI change | `add-page` | Pair with the frontend rule. Distinguish AdminPortal schema panels from custom operator/admin React pages; add `runtime-change` when platform/admin shell behavior changes. |
| Add or author a workflow | `create-workflow` | `add-workflow` is a planned alias. |
| Persistence / data contract / repo contract | `persistence-change` | Covers `data.json`, migrations, `repo.py`, and `ModuleContext.persistence`. Add `runtime-change` when runtime persistence behavior changes. Add `appgenerator-change` when generated persistence output changes. |
| Docs-only change | `docs-maintenance` | If the docs change a specific layer contract, also read that layer's rule. |
| Test-only change | owning surface skill | Use the owning surface skill when obvious. If unclear, start with `oss-contribution-review`. |
| CLI change | `oss-contribution-review` | No CLI-specific skill exists yet. If CLI scaffolding changes module/page/workflow contracts, also inspect the owning layer rule or skill. |
| Release/changelog change | `release-notes` | Use for `CHANGELOG.md`, release docs, versioning, or release-impact review. |
| Managed-capability support | `oss-contribution-review` | No dedicated `managed-capability-change` skill exists yet. Pair with the managed-capabilities rule; `managed-capability-change` remains planned. |
| Setup or local-dev guidance | `setup` | Use for installation, local runtime, and repo setup guidance. |
| If unsure or scope spans layers | `oss-contribution-review` | Use before or after the change to classify the impact. |

## Planned Focused Skills

- `managed-capability-change`
- `add-workflow` alias for `create-workflow`

## Routing Notes

- Build is `workflow_sequence`-driven.
- AppGenerator is one workflow inside the build sequence, not the whole build system.
- AgentGenerator is one workflow inside the build sequence, not the whole build system.
- `ExistingAppDiscovery` belongs to the brownfield flow.
- Use `appgenerator-change` as the primary skill for workflow-local AppGenerator prompts, file contracts, AppBuildPlan validation, app-bundle assembly, generated UI quality gates, and managed-capability facade planning. Add `factory-build-workflow-change` only when the change also affects `workflow_sequence`, `transitions[]`, `entrypoints[]`, or cross-workflow build ownership.
- Use `agentgenerator-change` as the primary skill for workflow-local AgentGenerator prompts, workflow-bundle scaffolds, universal prompt hooks, tool-planning contracts, and generated handoff or tool bundle guidance. Add `factory-build-workflow-change` only when the change also affects `workflow_sequence`, `transitions[]`, `entrypoints[]`, or cross-workflow build ownership.
- Use `existing-app-discovery-change` as the primary skill for workflow-local brownfield discovery, preload detectors, adoption classification, discovery artifact saving, and AppContext onboarding changes. Add `factory-build-workflow-change` only when the change also affects `extension_registry.json`, `workflow_sequence`, `transitions[]`, `entrypoints[]`, or cross-workflow sequence composition.
- Use `factory-build-workflow-change` for factory workflow changes that span sequence design, workflow ownership, artifact routing, or brownfield vs greenfield routing.
- Use `runtime-change` for auth, transport, platform, and other runtime substrate work unless the task is purely docs or tests.
- For test-only work, use the owning surface skill when obvious; otherwise start with `oss-contribution-review`.
- Use `oss-contribution-review` plus the managed-capabilities rule for managed-capability work until a dedicated skill exists.
- Task label note: "Build sequence / extension registry / journey composition" work routes through `factory-build-workflow-change`.
- When a planned skill is missing, start with `oss-contribution-review` and
  then inspect the owning files directly.

