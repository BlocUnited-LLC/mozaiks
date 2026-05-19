# Skills Index

Use this index to choose the closest skill before nontrivial work.

## Active Task Map

| Change type | Use this skill | Notes |
| --- | --- | --- |
| Runtime/platform change | `runtime-change` | Use `runtime-architecture-review` when you only need a pre-change or post-change boundary review. |
| Build sequence / factory workflow changes | `factory-build-workflow-change` | Use `build-sequence-change` when you only need a narrow sequence or journey-composition review. |
| AppGenerator-specific change | `factory-build-workflow-change` | Then inspect `factory_app/workflows/AppGenerator/` and the nearest AppGenerator docs/tests. |
| AgentGenerator-specific change | `factory-build-workflow-change` | Then inspect `factory_app/workflows/AgentGenerator/` and the nearest AgentGenerator docs/tests. |
| ExistingAppDiscovery change | `factory-build-workflow-change` | Then inspect `factory_app/workflows/ExistingAppDiscovery/` and the brownfield docs/tests. |
| Control-plane / refinement / harness routing | `control-plane-refinement-change` | Covers `app/config/ai.json`, `control_plane.yaml`, artifact routing, and checkpoint re-entry. |
| Add a deterministic backend module | `add-module` | Canonical module contract and module backend structure. |
| Add a page or custom route | `add-page` | Use for AppPageSchema or route-manifest work. |
| Add or author a workflow | `create-workflow` | `add-workflow` is a planned alias. |
| Persistence / database intent / repo contract | `persistence-change` | Covers `database_intent.json`, migrations, `repo.py`, and `ModuleContext.persistence`. |
| Hosted-pack support | planned `hosted-pack-change` | Until it exists, start with `oss-contribution-review` and inspect the facade and adapter docs/tests. |
| Docs or prompt-pack maintenance | `docs-maintenance` | Use for docs-only changes, link fixes, and prompt-pack hygiene. |
| Setup or local-dev guidance | `setup` | Use for installation, local runtime, and repo setup guidance. |
| If unsure or scope spans layers | `oss-contribution-review` | Use before or after the change to classify the impact. |

## Planned Focused Skills

- `appgenerator-change`
- `agentgenerator-change`
- `existing-app-discovery-change`
- `hosted-pack-change`
- `add-workflow` alias for `create-workflow`

## Routing Notes

- Build is `workflow_sequence`-driven.
- AppGenerator is one workflow inside the build sequence, not the whole build system.
- AgentGenerator is one workflow inside the build sequence, not the whole build system.
- `AppGenerator` is one workflow inside the build sequence, not the whole build system.
- `ExistingAppDiscovery` belongs to the brownfield flow.
- Use `factory-build-workflow-change` for factory workflow changes that span sequence design, workflow ownership, artifact routing, or brownfield vs greenfield routing.
- Legacy task label note: "Build sequence / extension registry / journey composition" work now routes through `factory-build-workflow-change`.
- When a planned skill is missing, start with `oss-contribution-review` and
  then inspect the owning files directly.