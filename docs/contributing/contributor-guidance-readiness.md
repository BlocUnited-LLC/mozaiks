# OSS Contributor Guidance Readiness

This checkpoint documents the current state of the Mozaiks OSS contributor
guidance operating system.

Use it to understand what contributor work is covered today, how common tasks
route through the current skill and rule system, what is intentionally deferred,
and which tests protect the guidance.

This document complements `CONTRIBUTING.md`, `.claude/skills/README.md`, and
the relevant `.claude/rules/*.md` files. It does not replace the architecture
docs.

## Current coverage

Primary covered surfaces today:

- Runtime and platform: `runtime-change`
- Factory build workflow sequence and cross-workflow routing: `factory-build-workflow-change`
- Control-plane and refinement: `control-plane-refinement-change`
- ExistingAppDiscovery and brownfield discovery: `existing-app-discovery-change`
- AppGenerator workflow-local changes: `appgenerator-change`
- AgentGenerator workflow-local changes: `agentgenerator-change`
- Module authoring and scaffolding: `add-module`
- Frontend pages and admin UI surfaces: `add-page` plus the frontend rule
- Persistence and data contract guidance: `persistence-change`
- Hosted-pack boundaries: `oss-contribution-review` plus the hosted-packs rule
- Docs, release, and testing guidance: `docs-maintenance`, `release-notes`, and `.claude/rules/testing.md`

Supporting guardrails are also in place through the rule pack for module
contracts, frontend and admin UI boundaries, persistence, hosted-pack boundaries,
docs, release notes, architecture placement, and final-report expectations.

## Task routing summary

| Task type | Primary skill | Companion rule or skill |
| --- | --- | --- |
| Runtime/platform | `runtime-change` | `runtime-architecture-review` for review-only scope |
| Auth change | `runtime-change` | runtime and architecture-boundary rules |
| AppGenerator-local | `appgenerator-change` | add `factory-build-workflow-change` only for `workflow_sequence`, `transitions[]`, `entrypoints[]`, or cross-workflow routing changes |
| AgentGenerator-local | `agentgenerator-change` | add `factory-build-workflow-change` only for `workflow_sequence`, `transitions[]`, `entrypoints[]`, or cross-workflow routing changes |
| ExistingAppDiscovery-local | `existing-app-discovery-change` | add `factory-build-workflow-change` only for `extension_registry.json`, `workflow_sequence`, `transitions[]`, `entrypoints[]`, or cross-workflow routing changes |
| Sequence/routing | `factory-build-workflow-change` | `build-sequence-change` for narrow journey-composition review |
| Control-plane/refinement | `control-plane-refinement-change` | add `factory-build-workflow-change` when `workflow_sequence` composition or `extension_registry.json` routing also changes |
| Module contract | `add-module` | use `runtime-change` for loader or runtime behavior changes; use `appgenerator-change` for generated module output changes |
| Frontend/admin UI | `add-page` | use the frontend rule; add `runtime-change` if platform or admin shell behavior changes |
| Persistence/data contract | `persistence-change` | add `runtime-change` for runtime persistence behavior; add `appgenerator-change` for generated persistence output |
| Docs-only | `docs-maintenance` | also read the owning layer rule when contract docs change |
| Release/changelog | `release-notes` | release-notes rule |
| Test-only | owning surface skill | use `oss-contribution-review` if the owning surface is unclear |
| CLI | `oss-contribution-review` | also inspect the owning layer rule or skill when CLI scaffolding changes module, page, or workflow contracts |
| Hosted-pack support | `oss-contribution-review` | hosted-packs rule for now |

## Known deferrals

- No dedicated `hosted-pack-change` skill exists yet.
- No dedicated CLI-change skill exists yet.
- Deeper AppGenerator or AgentGenerator subsystem skills can be added later if
  workflow-local change volume or subsystem complexity grows.
- ExistingAppDiscovery contributor guidance exists for brownfield workflow
  maintenance, but ExistingAppDiscovery service or productization is not active
  OSS service scope.
- Private hosted product repos are not contributor dependencies for this repo.

## Guidance validation

The current guidance system is protected by focused tests, including:

- `tests/test_contributor_quickstart.py`
- `tests/test_claude_guidance_operating_system.py`
- `tests/test_runtime_change_skill.py`
- `tests/test_factory_build_workflow_skill.py`
- `tests/test_control_plane_refinement_skill.py`
- `tests/test_existing_app_discovery_skill.py`
- `tests/test_appgenerator_change_skill.py`
- `tests/test_agentgenerator_change_skill.py`
- `tests/test_contributor_skill_routing_map.py`
- `tests/test_contributor_guidance_framing.py`
- `tests/test_module_reactions_docs_contract.py`
- `tests/test_admin_ui_two_tier_contract.py`

## Contributor instruction

For nontrivial OSS work:

1. Start with `CONTRIBUTING.md`.
2. Then use `.claude/skills/README.md` to pick the closest skill.
3. Read the relevant `.claude/rules/*.md` file for the affected layer.
4. Run the focused tests listed by the selected skill.
5. Include the correct final-report section from `.claude/rules/testing.md`.
6. If the owner is unclear, start with `oss-contribution-review`.

## Non-goals

- This guidance system does not replace `ARCHITECTURE.md` or the architecture docs.
- It does not authorize private hosted product logic in OSS code, docs, tests,
  or guidance.
- It does not make AppGenerator the entire build system.
- It does not make `workflow_sequence` a human-in-the-loop handoff.
