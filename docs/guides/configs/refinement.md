# Refinement

Mozaiks refinement has two app-facing files with different jobs:

- `app/config/refinement_policy.yaml` is runtime policy.
- `refinement_harness/config/harness.yaml` is an optional app-local harness
  bundle.

The Refinement Engine is the always-present runtime capability that routes and
validates app changes. The optional Refinement Harness gives an app additional
declarative routing, prompt, tool, validation, and promotion-policy metadata.

## `app/config/refinement_policy.yaml`

Use this file for:

- enabling or disabling app-local refinement policy
- model profiles
- classifier policy
- coding worker policy
- contract-surface refinement policy

Starter:

```yaml
schema_version: mozaiks.refinement.policy.v1
enabled: true
llm_profiles:
  classifier:
    purpose: Change classification for refinement routing.
    expected_behavior: Distinguish patch, design, feature, and core requests.
    llm_config:
      model: gpt-5-nano
      temperature: 0
  codegen:
    purpose: Scoped code and contract-surface refinement.
    expected_behavior: Produce bounded app artifact changes.
    llm_config:
      model: gpt-5.2-codex
      temperature: 0.1
classifier:
  enabled: true
  llm_profile: classifier
coding:
  enabled: true
  llm_profile: codegen
contract_surface:
  enabled: true
  llm_profile: codegen
```

Do not put ask/chat startup, workflow entry points, prompt bodies, checkpoint
handlers, or workflow sequence definitions in this file.

## `refinement_harness/config/harness.yaml`

Use the optional harness bundle when the app needs to declare:

- app-specific refinement goals
- workflow sequences
- checkpoint behavior
- prompt and tool references
- validation gates
- promotion policy

Keep the harness declarative. Do not put model secrets, raw provider config, or
ordinary app startup here.

## What Goes Where

| Concern | File |
|---------|------|
| Ask/chat/workflow startup | `app/config/ai.json` |
| Refinement model profiles | `app/config/refinement_policy.yaml` |
| Refinement routing bundle | `refinement_harness/config/harness.yaml` |
| Workflow definitions | `workflows/{workflow_id}/` |
| App data affected by refinement | `app/data/contract.json` and `app/data/migrations/` |
| Module behavior affected by refinement | `app/modules/{module_id}/` |

See also [Refinement Policy](../extending-ai-functionality/03-refinement-policy.md),
[Refinement Harness](../extending-ai-functionality/04-refinement-harness.md), and
[Refinement Engine](../../architecture/workflows/refinement-engine.md).
