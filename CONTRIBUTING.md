# Contributing to Mozaiks

Mozaiks is the OSS runtime, platform, Studio, and factory framework repo.
`factory_app` is the first-party builder/reference app workspace that dogfoods
the same contracts external app workspaces consume.

## Start Here

Use this order before nontrivial work:

1. Read [.claude/skills/README.md](.claude/skills/README.md) to choose the closest task skill.
2. Read the matching [.claude/rules](.claude/rules) files for the layer you are changing.
3. Use [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md) for repo-wide agent and contributor rules.

If scope spans layers or the right owner is unclear, start with the
`oss-contribution-review` skill.

## Common Task Map

- Runtime or platform change: use `runtime-change` plus the runtime and architecture-boundary rules. Use `runtime-architecture-review` when you need a review-only scope or boundary pass before or after edits.
- Build workflow sequence change: use `factory-build-workflow-change` plus the factory build workflow rules. Use `build-sequence-change` when you only need a narrow sequence or journey-composition review.
- AppGenerator-specific change: use `factory-build-workflow-change`, then inspect `factory_app/workflows/AppGenerator/` and the nearest AppGenerator docs/tests.
- AgentGenerator-specific change: use `factory-build-workflow-change`, then inspect `factory_app/workflows/AgentGenerator/` and the nearest AgentGenerator docs/tests.
- ExistingAppDiscovery or brownfield change: use `factory-build-workflow-change`, then inspect `factory_app/workflows/ExistingAppDiscovery/` and the brownfield docs/tests.
- Control-plane or refinement change: use `control-plane-refinement-change` plus the control-plane refinement rule.
- Module contract change: use the modules rule and related module contract tests.
- Page or frontend change: use the frontend rule and `add-page` when appropriate.
- Persistence change: use `persistence-change` plus the persistence rule.
- Hosted-pack support change: use the hosted-packs rule; if a focused skill is still missing, start with `oss-contribution-review`.
- Unsure: use `oss-contribution-review` first.

## Build And Refinement Truth

- Build is `workflow_sequence`-driven through `factory_app/workflows/extended_orchestration/extension_registry.json`.
- `AppGenerator` is one workflow inside that build system, not the whole build.
- `ValueEngine`, `ThemeCapture`, `DesignDocs`, `AgentGenerator`, and `AppGenerator` have separate responsibilities inside those sequences.
- `ExistingAppDiscovery` belongs to the brownfield flow.
- Refinement today is checkpoint and control-plane re-entry driven by `app/config/ai.json` plus the selected `control_plane.yaml` pack, not a dedicated `RefinementWorkflow`.
- `workflow_sequence` is not a human-in-the-loop handoff. Keep sequences, transitions, entrypoints, and workflow-local `handoffs.yaml` separate.

## Final Report Requirements

Every nontrivial change should include `Tests run` plus the relevant sections
from [.claude/rules/testing.md](.claude/rules/testing.md):

- `OSS Change Impact`
- `Build Workflow Sequence Impact` when sequence, transition, or entry routing changed
- `Control-Plane / Refinement Impact` when checkpoint routing or refinement behavior changed
- `Module Contract Impact` when module contracts or module loader expectations changed
- `Hosted Pack Boundary Check` when hosted-pack, facade, or adapter boundaries changed

## Focused Tests

Prefer the narrowest test slice that matches the layer you changed.

- Docs and guidance changes should use focused hygiene tests.
- Do not default to broad unrelated test runs when a narrower slice can falsify the change.
- Update docs and tests together when contributor guidance changes.

Focused guidance validation:

```bash
python -m pytest tests/test_contributor_guidance_framing.py tests/test_module_reactions_docs_contract.py tests/test_admin_ui_two_tier_contract.py tests/test_claude_guidance_operating_system.py tests/test_contributor_quickstart.py -q
```

## Boundary Warnings

- Do not copy private hosted product logic into the OSS repo.
- Use provider-neutral examples in public contributor guidance.
- Do not treat `AppGenerator` as the whole build system.
- Do not treat `workflow_sequence` as HITL handoff routing.
- Do not reintroduce `backend/models.py` as canonical persistence structure.
- Do not reintroduce `contracts/subscriptions.yaml` as the canonical module reaction contract.
- Do not route contributors toward `app/capability_packs`, `transport.py`, or direct hosted internals as current canonical extension points.

## Development Setup

```bash
pip install -e .[dev]
```

## Pull Request Expectations

- Explain scope and motivation.
- Call out public API changes in `mozaiksai/`.
- Update architecture or contributor docs when behavior, paths, or contributor workflow changed.
- Add or update focused tests for the touched surface.

## Commit Hygiene

- Keep commits focused.
- Avoid unrelated refactors in the same PR.
- Do not include generated noise unless required.

## Security

Do not commit secrets, production tokens, or private keys.
