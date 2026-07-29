---
title: App-Local Refinement Harness
status: Authoritative - Pre-Production, Canonical Contract
created: 2026-06-02
updated: 2026-07-29
depends_on: ../workflows/refinement-harness-architecture.md, ../workflows/refinement-engine.md
---

# App-Local Refinement Harness

Most Mozaiks apps do not need an app-local refinement harness. The packaged OSS
default `mozaiks.default_refinement_harness` owns the standard artifact routes,
checkpoints, prompts, tools, and deterministic policies used by generated apps.

Generated apps that enable refinement should carry an explicit
`refinement_harness/config/harness.yaml` overlay. When there is no app-specific
delta, the overlay is just `extends: mozaiks.default_refinement_harness` plus
`overrides: {}`. The app-local file is an overlay, not a copied pack.

## File Layout

```text
app/
  config/
    ai.json
    refinement_policy.yaml
refinement_harness/
  config/
    harness.yaml
    tools.yaml
    policies.yaml
  prompts/
    *.yaml
workflows/
  extended_orchestration/
    extension_registry.json
```

Required for refinement:

- `app/config/refinement_policy.yaml`
- `refinement_harness/config/harness.yaml`

Optional overlay files:

- `refinement_harness/config/tools.yaml`
- `refinement_harness/config/policies.yaml`
- `refinement_harness/prompts/*.yaml`

`app/config/ai.json` owns ask/chat/workflow startup. It does not declare
refinement routing, checkpoint prompts, or scoped coding policy.

## Minimal Policy

```yaml
schema_version: mozaiks.refinement.policy.v1
enabled: true
profile: default

llm_profiles:
  classifier:
    purpose: Classify refinement requests into stable change classes.
    expected_behavior: deterministic structured classification
    llm_config:
      model: gpt-5-nano
      temperature: 0.0

  codegen:
    purpose: Generate scoped code or artifact patches.
    expected_behavior: deterministic code and artifact generation
    llm_config:
      model: gpt-5.2-codex
      temperature: 0.1

classifier:
  enabled: true
  llm_profile: classifier

coding:
  enabled: true
  llm_profile: codegen
```

Add `contract_surface` only when the app uses contract-surface planning:

```yaml
contract_surface:
  enabled: true
  llm_profile: codegen
```

## Minimal Overlay

Use this when the packaged default is acceptable:

```yaml
schema_version: mozaiks.refinement_harness.v1
extends: mozaiks.default_refinement_harness
overrides: {}
```

Add only real app-specific deltas, such as an app-local context tool:

```yaml
schema_version: mozaiks.refinement_harness.v1
extends: mozaiks.default_refinement_harness
overrides:
  checkpoints:
    - event: coding_requested
      append_tool_ids:
        - app_local_context
```

The default harness continues to supply the standard routes, prompts, tools, and
checkpoint declarations. The overlay only states what changes.

## Routing Deltas

Add or adjust artifact routes under `overrides.routing.artifacts`.
`artifact_kind` is the merge key.

```yaml
schema_version: mozaiks.refinement_harness.v1
extends: mozaiks.default_refinement_harness
overrides:
  routing:
    artifacts:
      - artifact_kind: report_bundle
        label: report bundle
        routes:
          patch:
            workflow_sequence: report_patch
          design:
            workflow_sequence: report_revision
          feature:
            workflow_sequence: report_revision
          core:
            workflow_sequence: full_rebuild
```

Every `workflow_sequence` referenced by the effective harness must exist in
`workflows/extended_orchestration/extension_registry.json` and must declare
`affected_declarative_families`.

## Tool Deltas

App-local tools live in `refinement_harness/config/tools.yaml`. They merge with
the packaged tool manifest by `id`.

```yaml
schema_version: mozaiks.refinement_harness.tools.v1
tools:
  - id: app_local_context
    kind: context_tool
    description: Load app-local refinement context.
    entrypoint: app.refinement_tools:app_local_context
    available_to:
      - coding_requested
```

Do not add custom harness runtime Python. If a tool is generally useful to
future generated apps, add it to the OSS default harness instead.

## Prompt Deltas

Prompt overrides must be referenced explicitly from `overrides.prompts`.

```yaml
schema_version: mozaiks.refinement_harness.v1
extends: mozaiks.default_refinement_harness
overrides:
  prompts:
    coding_refinement_system: refinement_harness/prompts/coding_refinement_system.yaml
```

The prompt file must declare the same id:

```yaml
id: coding_refinement_system
content: |
  You are the scoped coding refinement worker for this app.
```

## Policy Deltas

Use `refinement_harness/config/policies.yaml` only for deterministic scope
policy differences:

```yaml
schema_version: mozaiks.refinement_harness.policies.v1
scope:
  max_selected_paths: 5
```

Unspecified policy fields keep the packaged default values.

## Merge Rules

- `schema_version` must match the packaged schema.
- `extends` must be `mozaiks.default_refinement_harness`.
- manifest deltas live under `overrides`.
- scalar values override the packaged value.
- object values merge recursively by key.
- `routing.artifacts` merge by `artifact_kind`.
- `checkpoints` merge by `event`.
- checkpoint `tool_ids` replace the packaged list.
- checkpoint `append_tool_ids` adds tool ids while preserving packaged order.
- `config/tools.yaml` merges tools by `id`.
- `config/policies.yaml` merges policy objects by key.
- `overrides.prompts` maps prompt id to app-local prompt path.

## What Not To Generate

Generated refinement harness overlays are declarative only:

- no `module.yaml`; the harness is not a module
- no `app/modules/*`
- no `backend/control_plane/*.py`
- no custom harness Python
- no business-domain logic in generic prompts
- no model names in prompt content; model config belongs in `refinement_policy.yaml`
- no `affected_workflows` or `affected_declarative_families` in harness routes
- no `context_variables.yaml`; the harness is not a workflow
