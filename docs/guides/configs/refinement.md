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
      - get_app_intelligence_context
      - get_stale_artifact_families
  - event: scope_requested
    prompt_id: coding_scope_selection_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary
      - get_app_intelligence_context
      - get_artifact_workspace_catalog
      - get_context_graph_catalog
      - search_app_source_context
  - event: contract_surface_requested
    prompt_id: contract_surface_selection_system
    tool_ids:
      - get_contract_surface_context
      - get_app_intelligence_context
      - search_app_source_context
  - event: coding_requested
    prompt_id: coding_refinement_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary
      - get_app_intelligence_context
      - get_artifact_workspace_scope
      - get_context_graph_scope
      - search_app_source_context
      - read_app_source_file
      - get_related_app_source_files
```

Every `workflow_sequence` referenced by the harness must exist in
`workflows/extended_orchestration/extension_registry.json`.

## Code Context By Checkpoint

Refinement should use code context progressively:

| Checkpoint | Context tools | Purpose |
| --- | --- | --- |
| `request_submitted` | `get_revision_context`, `get_artifact_summary`, `get_app_intelligence_context`, `get_stale_artifact_families` | classify request scope from builder state, App Intelligence freshness, and staleness, without raw code snippets |
| `scope_requested` | `get_app_intelligence_context`, `get_artifact_workspace_catalog`, `get_context_graph_catalog`, `search_app_source_context` | choose the smallest safe file scope from app shape, graph relationships, and bounded source search |
| `contract_surface_requested` | `get_contract_surface_context`, `get_app_intelligence_context`, `search_app_source_context` | map the request to module/page/workflow/config contract surfaces |
| `coding_requested` | `get_artifact_summary`, `get_app_intelligence_context`, `get_artifact_workspace_scope`, `get_context_graph_scope`, `read_app_source_file`, `get_related_app_source_files`, `search_app_source_context` | patch only explicit scoped files while using exact source reads and related files as read-only context |

Do not dump a repository into prompts. The current `AppIntelligenceSnapshot`
summarizes architecture and ownership; `SourceContextBundle` stores selected
redacted files, chunks, symbols, and imports; agents retrieve exact evidence
through tools only when their checkpoint needs it.

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
