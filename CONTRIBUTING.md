# Contributing to Mozaiks

Mozaiks is the OSS runtime, platform, Studio, and factory framework repo.
`factory_app` is the first-party builder/reference app workspace that dogfoods
the same contracts external app workspaces consume.

All participation in this repository is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Quickstart: Your First Pull Request

Follow this path for any human-authored contribution — a bug fix, a docs
change, a new test, or a small feature:

1. **Fork** the repository and clone your fork.
2. **Create a branch** for your change, e.g. `git checkout -b fix/short-description`.
3. **Install development dependencies**:

   ```bash
   pip install -e ".[dev]"
   ```

4. **Make a focused change.** Keep the diff scoped to one thing and avoid unrelated refactors.
5. **Sign off your commits** with `git commit -s`. This certifies you have the
   right to contribute the change under the
   [Developer Certificate of Origin](DCO.md) - there is no CLA and no rights
   assignment. CI checks for it, and forgetting is easy to fix after the fact.
6. **Run the relevant tests** for what you changed. Use the narrowest test slice that covers your change (see [Running Tests Locally](#running-tests-locally) below), or run the full suite with `pytest`.
7. **Open a pull request** against `main` using the pull request template. Describe what changed, why, and what you tested.

Using an AI coding agent (Claude Code, Cursor, Copilot, or similar)? Read the
[AI Policy](.github/AI_POLICY.md) first — it is short, it applies to
maintainers and their agents too, and it explains what we ask of any
contribution regardless of how it was written. Then see
[Working With an AI Coding Agent](#working-with-an-ai-coding-agent-optional)
below for the repo's optional agent routing system, which is tooling this repo
supports rather than a prerequisite for contributing.

## Where to Start

Looking for something to work on:

Browse the
**[good first issue](https://github.com/BlocUnited-LLC/mozaiks/labels/good%20first%20issue)**
label — scoped, self-contained work, written with file and line references so
you are not hunting for context.

Most of these need no MongoDB, Node.js, or LLM API key — see
[What You Can Contribute Without Extra Setup](#what-you-can-contribute-without-extra-setup).

### Claim it before you start

**Comment on the issue saying you are taking it, and wait for a maintainer to
assign it to you.** GitHub does not let contributors self-assign, so the
assignment has to come from our side; it is usually quick.

This matters more than it looks. Two people have already built the same fix
half an hour apart because nothing here said to claim first, and one of them
lost the work. That was our fault, not theirs, and this section exists so it
does not happen again. An issue with an assignee is taken; an issue without one
is free.

If an issue is assigned but has gone quiet for a couple of weeks, comment and
ask — people's circumstances change and we would rather hand it on than let it
sit.

### A note on the suggested approach

Many issues here include a suggested fix. That is a starting point written by
someone who may have been wrong, not a specification. Read the surrounding code
first, and if the suggestion looks wrong, **say so on the issue** — noticing
that is worth more to us than implementing it faithfully.

There is no bounty program; see [Bounties](#bounties).

## What You Can Contribute Without Extra Setup

Running the full Mozaiks Studio stack needs MongoDB, Node.js, and an LLM API
key. Most first contributions need none of that:

- **Documentation** changes (`docs/`, `README.md`, `CONTRIBUTING.md`, and other
  Markdown) only need Python and the docs extra to preview locally:

  ```bash
  pip install -e ".[docs]"
  python -m mkdocs serve
  ```

- **Most Python tests** run with no external services. `tests/conftest.py`
  automatically skips the tests that need a real app workspace
  (`MOZAIKS_APP_WORKSPACE_PATH` or `PLATFORM_PATH`) — you do not need to set
  those to add or fix an ordinary test.
- **Many `mozaiks_cli` changes** can be developed and verified through the
  CLI's own unit tests without MongoDB, Node.js, or a running Studio instance.

You only need Docker/MongoDB, Node.js 18+, and an LLM API key when you are
changing or manually verifying behavior that talks to a real database, a real
frontend build, or a real LLM call. See [Local Setup](docs/local-setup.md) when
you get there.

## Running Tests Locally

You do not need to run the entire test suite for every change. Pick the
focused test file(s) that cover what you touched:

```bash
python -m pytest tests/test_your_file.py -q
```

**Coverage gate note:** The local `pyproject.toml` configuration sets a
repository-wide minimum coverage threshold of 30% (`--cov-fail-under=30`,
measured against `mozaiksai`). However, **CI enforces a stricter 70% gate**
(`--cov-fail-under=70` in `.github/workflows/ci.yml`). CI is authoritative —
your pull request must pass the 70% threshold regardless of local results.

Running a narrow test file against the global threshold will often report a
coverage failure even when every test you ran passes. That failure reflects
the coverage math for the whole package, not a problem with your change.

To run a focused slice without tripping the repository-wide coverage gate,
use the verified command:

```bash
python -m pytest tests/test_your_file.py -q --no-cov
```

**CI remains authoritative.** `--no-cov` is a local convenience for fast
iteration on a narrow slice. It does not weaken what is enforced in CI — the
full test suite runs in CI with the 70% coverage gate enforced, and your pull
request must pass CI regardless of what you ran locally.

## Working With an AI Coding Agent (Optional)

> **Before anything below:** the [AI Policy](.github/AI_POLICY.md) covers what
> we ask of AI-assisted contributions — you understand and have tested the
> change, the description matches the diff, and you can discuss it in review.
> It applies to maintainer agents on `cc/` and `codex/` branches on exactly the
> same terms. It also explains the boundary question agents get wrong most
> often here: which changes belong in this repository and which belong in the
> hosted product.

This repo also maintains a skill/rule routing system that AI coding agents
(Claude Code, Cursor, Copilot, and similar tools) use to find the right
context before nontrivial changes. Human contributors may use it too, but
nothing below is required to complete the Quickstart above.

### Start Here

Use this order before nontrivial work:

1. Read [.claude/skills/README.md](.claude/skills/README.md) to choose the closest task skill.
2. Read the matching [.claude/rules](.claude/rules) files for the layer you are changing.
3. Use [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md) for repo-wide agent and contributor rules.

If scope spans layers or the right owner is unclear, start with the
`oss-contribution-review` skill.

### Common Task Map

- Runtime or platform change: use `runtime-change` plus the runtime and architecture-boundary rules. Use `runtime-architecture-review` when you need a review-only scope or boundary pass before or after edits.
- Auth change: use `runtime-change` unless the task is purely docs or tests.
- Build workflow sequence change: use `factory-build-workflow-change` plus the factory build workflow rules. Use `build-sequence-change` when you only need a narrow sequence or journey-composition review.
- AppGenerator-specific change: use `appgenerator-change`, then inspect `factory_app/workflows/AppGenerator/` and the nearest AppGenerator docs/tests. Add `factory-build-workflow-change` as a companion skill only when the change widens into `extension_registry.json`, sequence design, transitions, entrypoints, or cross-workflow factory composition.
- AgentGenerator-specific change: use `agentgenerator-change`, then inspect `factory_app/workflows/AgentGenerator/` and the nearest AgentGenerator docs/tests. Add `factory-build-workflow-change` as a companion skill only when the change widens into `extension_registry.json`, sequence design, transitions, entrypoints, or cross-workflow factory composition.
- ExistingAppDiscovery or brownfield change: use `existing-app-discovery-change`, then inspect `factory_app/workflows/ExistingAppDiscovery/` and the brownfield docs/tests. Add `factory-build-workflow-change` as a companion skill only when the change widens into `extension_registry.json`, sequence design, transitions, entrypoints, or cross-workflow factory composition.
- Refinement Engine or refinement harness change: use `control-plane-refinement-change` plus the refinement rule. Add `factory-build-workflow-change` too when `workflow_sequence` composition or `extension_registry.json` routing changes.
- Module contract change: use `add-module` for module authoring or scaffolding changes. Use `runtime-change` if module loader, executor, or runtime behavior changes. Use `appgenerator-change` if generated module output changes.
- Add a deterministic backend module: use `add-module`.
- Page or frontend change: use the frontend rule and `add-page` when appropriate.
- Admin UI change: use `add-page` plus the frontend rule for custom operator/admin React pages. Distinguish AdminPortal schema panels from custom operator React routes. If platform/admin shell behavior changes, use `runtime-change` too.
- Persistence change: use `persistence-change` plus the persistence rule. Add `runtime-change` if `ModuleContext.persistence` or runtime persistence behavior changes. Add `appgenerator-change` if generated database intent or module persistence output changes.
- Docs-only change: use `docs-maintenance`. If docs change a specific layer contract, also read that layer's rule.
- Test-only change: use the owning surface skill when obvious. Runtime tests go to `runtime-change`, AppGenerator tests go to `appgenerator-change`, and workflow sequence tests go to `factory-build-workflow-change`. If the owner is unclear, use `oss-contribution-review`.
- CLI change: use `oss-contribution-review` for now. If CLI scaffolding changes module, page, or workflow contracts, also inspect the owning layer rule or skill.
- Release/changelog change: use `release-notes`.
- Managed-capability support change: use `oss-contribution-review` plus the managed-capabilities rule for now; no dedicated `managed-capability-change` skill exists yet.
- Unsure: use `oss-contribution-review` first.

### Build And Refinement Truth

- Build is `workflow_sequence`-driven through `factory_app/workflows/extended_orchestration/extension_registry.json`.
- `AppGenerator` is one workflow inside that build system, not the whole build.
- `ValueEngine`, `ThemeCapture`, `DesignDocs`, `AgentGenerator`, and `AppGenerator` have separate responsibilities inside those sequences.
- `ExistingAppDiscovery` belongs to the brownfield flow.
- Refinement today is checkpoint-driven re-entry through `app/config/refinement_policy.yaml` runtime policy and the selected `refinement_harness/config/harness.yaml` pack, with normal chat/workflow startup still in `app/config/ai.json`; it is not a dedicated `RefinementWorkflow`.
- `workflow_sequence` is not a human-in-the-loop handoff. Keep sequences, transitions, entrypoints, and workflow-local `transition_graph.yaml` separate.

### Final Report Requirements

Every nontrivial change should include `Tests run` plus the relevant sections
from [.claude/rules/testing.md](.claude/rules/testing.md):

- `OSS Change Impact`
- `Build Workflow Sequence Impact` when sequence, transition, or entry routing changed
- `Control-Plane / Refinement Impact` when checkpoint routing or refinement behavior changed
- `Module Contract Impact` when module contracts or module loader expectations changed
- `Managed Capability Boundary Check` when managed-capability, facade, or adapter boundaries changed

### Focused Tests

Prefer the narrowest test slice that matches the layer you changed.

- Docs and guidance changes should use focused hygiene tests.
- Do not default to broad unrelated test runs when a narrower slice can falsify the change.
- Update docs and tests together when contributor guidance changes.

Focused guidance validation:

```bash
python -m pytest tests/test_contributor_guidance_framing.py tests/test_module_reactions_docs_contract.py tests/test_admin_ui_two_tier_contract.py tests/test_claude_guidance_operating_system.py tests/test_contributor_quickstart.py tests/test_runtime_change_skill.py tests/test_factory_build_workflow_skill.py tests/test_control_plane_refinement_skill.py tests/test_existing_app_discovery_skill.py tests/test_appgenerator_change_skill.py tests/test_agentgenerator_change_skill.py tests/test_contributor_skill_routing_map.py -q
```

## Boundary Warnings

- Do not copy private hosted product logic into the OSS repo.
- Use provider-neutral examples in public contributor guidance.
- Do not treat `AppGenerator` as the whole build system.
- Do not treat `workflow_sequence` as HITL handoff routing.
- Do not reintroduce `backend/models.py` as canonical persistence structure.
- Do not author `contracts/subscriptions.yaml`; use `contracts/reactions.yaml`.
- Do not route contributors toward `app/capability_packs`, `transport.py`, or direct provider internals as current canonical extension points.

## Pull Request Expectations

Opening a pull request uses the repository's pull request template, which asks
for the related issue (if any), a summary, the tests you ran, screenshots for
UI changes, and confirmation that no private hosted-product logic was
introduced. In addition:

- Explain scope and motivation.
- Call out public API changes in `mozaiksai/`.
- Update architecture or contributor docs when behavior, paths, or contributor workflow changed.
- Add or update focused tests for the touched surface.

## Commit Hygiene

- Keep commits focused.
- Avoid unrelated refactors in the same PR.
- Do not include generated noise unless required.

## Developer Certificate of Origin

Mozaiks has **no CLA**. You keep the copyright to your work and assign us
nothing. Instead, every commit carries a one-line certification that you have
the right to contribute it, which is what the
[Developer Certificate of Origin](DCO.md) says.

`git commit -s` adds it for you:

```
Signed-off-by: Your Name <your.email@example.com>
```

Set `git config user.name` and `git config user.email` first, since the line is
generated from them.

Forgot? Nothing is lost:

```bash
# most recent commit
git commit --amend -s --no-edit && git push --force-with-lease

# every commit on your branch
git rebase --signoff origin/main && git push --force-with-lease
```

The `DCO` check on your pull request prints the exact command for your branch
when it fails.

## Getting Help

- **[Discord](https://discord.gg/Qnsywad9kp)** — questions, design discussion,
  and a good place to ask before writing code if you are unsure whether an
  approach fits.
- **[Docs](https://docs.mozaiks.ai)** — architecture, contracts, and guides.
- **The issue itself** — if an issue is unclear or its suggested approach looks
  wrong, say so there. That is useful feedback, not an interruption.

## Bounties

Mozaiks does not run a bounty program. Issues here carry no payment, including
those labeled `good first issue`, and we cannot act on requests for payment
attached to a pull request.

Contributions are voluntary. We think that is worth stating plainly rather than
leaving people to guess, since some ecosystems do attach bounties to issues and
the absence of a policy reads as ambiguity rather than as an answer.

## Security

Do not commit secrets, production tokens, or private keys. To report a security
vulnerability, see [SECURITY.md](SECURITY.md) instead of opening a public issue.

