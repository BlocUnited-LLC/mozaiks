# Refinement Policy

`app/config/refinement_policy.yaml` is the app-local runtime policy for the
Refinement Engine. It decides whether app-local refinement features are enabled and which model
profiles its capabilities use.

Use it for:

- `enabled`
- `llm_profiles`
- `classifier`
- `coding`
- `contract_surface`

Example:

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
    purpose: Scoped coding and contract-surface refinement.
    expected_behavior: Produce bounded repair or extension plans.
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

This file selects runtime policy and model budgets. It does not declare
checkpoint handlers, workflow sequences, prompt content, or app startup.

## Field Selection

Set `enabled: true` only when the app needs artifact-aware refinement or
lifecycle routing. Leave the file absent, or set `enabled: false`, for ordinary
ask/chat/workflow startup.

Define `llm_profiles` from the canonical profile ids Mozaiks understands:

- `classifier` for request classification.
- `impact_analyzer` for artifact impact analysis.
- `architecture` for concept-level planning.
- `planner_replanner` for higher-scope refinement planning.
- `codegen` for scoped code or artifact patches.
- `reviewer_validator` for review and validation.

Then point each capability at one of those profile ids:

- `classifier.llm_profile` powers `request_submitted`.
- `coding.llm_profile` powers `scope_requested` and `coding_requested`.
- `contract_surface.llm_profile` powers `contract_surface_requested`.

Do not add a `profile: default` field. Runtime profiles are now expressed by
the explicit capability-to-LLM mappings above.
