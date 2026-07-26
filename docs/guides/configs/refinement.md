# Refinement

Refinement lets an app route change requests against generated artifacts. It is
optional. Most CRUD apps, fixed workflow apps, and simple assistants only need
`app/config/ai.json`.

Use refinement when the app needs routed revisions, scoped artifact patches,
checkpoint decisions, or workflow re-entry based on request meaning.

## Files

| File | Owns |
|------|------|
| `app/config/refinement_policy.yaml` | Enables refinement capabilities and selects model profiles. |
| `refinement_harness/config/harness.yaml` | Maps artifact kinds and change classes to workflow sequences. |
| `refinement_harness/config/tools.yaml` | Names the deterministic tools available to checkpoint handlers. |
| `refinement_harness/prompts/*.yaml` | Prompt text referenced by checkpoint `prompt_id` values. |
| `workflows/extended_orchestration/extension_registry.json` | Workflow sequences that the harness can route into. |

## Minimal Policy

```yaml
schema_version: mozaiks.refinement.policy.v1
enabled: true

llm_profiles:
  classifier:
    purpose: Classify refinement requests into stable change classes.
    expected_behavior: deterministic structured classification
    llm_config:
      api_type: openai
      model: gpt-5-nano
  codegen:
    purpose: Generate scoped code or artifact patches.
    expected_behavior: deterministic code and artifact generation
    llm_config:
      api_type: openai
      model: gpt-5.2-codex
      temperature: 0.1

classifier:
  enabled: true
  llm_profile: classifier
coding:
  enabled: true
  llm_profile: codegen
```

## Minimal Harness

```yaml
schema_version: mozaiks.refinement_harness.v1
routing:
  default_artifact_kind: app_bundle
  artifacts:
    - artifact_kind: app_bundle
      label: app bundle
      routes:
        patch:
          workflow_sequence: app_revision
        design:
          workflow_sequence: app_surface_revision
        feature:
          workflow_sequence: app_revision
        core:
          workflow_sequence: full_rebuild

checkpoints:
  - event: request_submitted
    prompt_id: change_classifier_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary
  - event: coding_requested
    prompt_id: coding_refinement_system
    tool_ids:
      - get_revision_context
      - get_artifact_workspace_scope
```

Every `workflow_sequence` referenced by the harness must exist in
`workflows/extended_orchestration/extension_registry.json`.

## Boundaries

| Concern | File |
|---------|------|
| Ask/chat/workflow startup | `app/config/ai.json` |
| Model profile selection | `app/config/refinement_policy.yaml` |
| Artifact routing and checkpoints | `refinement_harness/config/harness.yaml` |
| Sequence impact metadata | `workflows/extended_orchestration/extension_registry.json` |

## Read Next

- [AI Startup](ai-startup.md)
- [Workflow Registry](../extending-ai-functionality/05-workflow-sequences.md)
- [Refinement Harness Architecture](../../architecture/workflows/refinement-harness-architecture.md)
