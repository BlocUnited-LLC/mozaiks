---
title: App-Local Refinement Harness
status: Authoritative - Pre-Production, Canonical Contract
created: 2026-06-02
depends_on: ../workflows/refinement-harness-architecture.md, ../workflows/refinement-engine.md
---

# App-Local Refinement Harness

This document defines what an app-local refinement harness looks like for a
generated app workspace. It is the canonical reference for AppGenerator when
emitting a `refinement_harness` build task.

Read [refinement-harness-architecture.md](../workflows/refinement-harness-architecture.md)
first — it covers the ownership model, pack model, and AG2 implementation
details that this document builds on.

---

## When An App Needs A Refinement Harness

Most generated apps do **not** need an app-local refinement harness. Default to
ordinary workflow launches, module actions, and `extension_registry.json`
workflow sequences.

Add an app-local pack only when the generated app explicitly needs:

- checkpointed refinement routing (classify → route → decide → patch/plan)
- scoped coding worker support (bounded patch refinement against owned files)
- contract surface planning (map a request to specific contract surfaces before
  re-entering a workflow sequence)
- multi-artifact routing (concept, design_docs, workflow_bundle, app_bundle each
  needing separate change-class routes)

If the app only needs to launch a single workflow in response to user input,
use `extension_registry.json` sequences and transitions instead.

---

## File Layout

A generated app refinement harness lives at the workspace root:

```text
app/
  config/
    refinement_policy.yaml  required — LLM profiles and feature flags

refinement_harness/
  config/
    harness.yaml    required — harness manifest, routing, checkpoints
    tools.yaml            required — context tool declarations
    policies.yaml         optional — deterministic scope bounds
  prompts/
    change_classifier_system.yaml     required when request_submitted is declared
    coding_scope_selection_system.yaml  required when scope_requested is declared
    coding_refinement_system.yaml       required when coding_requested is declared
    contract_surface_selection_system.yaml  required when contract_surface_requested is declared
```

`app/config/refinement_policy.yaml` enables app-local refinement capabilities.
It is not part of the harness directory; it lives with the other app config.

---

## `app/config/refinement_policy.yaml` — Refinement Policy

The refinement policy enables app-local refinement capabilities and maps each
capability to an LLM profile. It does not declare routes, checkpoints, or
prompts; those belong in `refinement_harness/config/harness.yaml` and
`refinement_harness/prompts/*.yaml`.

```yaml
schema_version: mozaiks.refinement.policy.v1
enabled: true
profile: default

llm_profiles:
  classifier:
    purpose: Classify refinement requests into the stable change classes.
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

To override the LLM model for a specific capability, add `llm_config` to that
capability in `app/config/refinement_policy.yaml`. Capability-level
`llm_config` overrides the referenced profile and should be used only when the
app needs a different model than the policy default.

Add `contract_surface` when `contract_surface_requested` is included:

```yaml
  contract_surface:
    enabled: true
    llm_profile: codegen
```

---

## `refinement_harness/config/harness.yaml` — Minimal Starter (app_bundle only)

The minimal pack for a generated app that supports `app_bundle` refinement with
patch coding support:

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

  - event: route_requested

  - event: decision_requested

  - event: scope_requested
    prompt_id: coding_scope_selection_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary
      - get_artifact_workspace_catalog

  - event: coding_requested
    prompt_id: coding_refinement_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary
      - get_artifact_workspace_scope
```

The `workflow_sequence` ids must exist in the app's
`workflows/extended_orchestration/extension_registry.json`.
Runtime implementation entrypoints are supplied by the refinement engine, so
generated apps do not declare implementation classes in `harness.yaml`.

### Adding Contract Surface Planning

To add contract surface planning (for feature/design refinements that need
surface-level targeting before workflow re-entry), add the checkpoint:

```yaml
  - event: contract_surface_requested
    prompt_id: contract_surface_selection_system
    tool_ids:
      - get_contract_surface_context
```

And add `contract_surface` to `refinement_policy.yaml` as shown above.

### Multi-Artifact Routing

For apps with multiple artifact kinds, add each as a separate entry under
`routing.artifacts`. Each artifact kind routes independently:

```yaml
routing:
  default_artifact_kind: app_bundle
  artifacts:
    - artifact_kind: concept
      label: concept
      routes:
        patch:
          workflow_sequence: concept_patch
        design:
          workflow_sequence: full_rebuild
        feature:
          workflow_sequence: full_rebuild
        core:
          workflow_sequence: conceptual_replan
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
```

---

## `refinement_harness/config/tools.yaml` — Minimal Starter

Context tools used by checkpoints. The minimal set for the starter pack above:

```yaml
schema_version: mozaiks.refinement_harness.tools.v1
tools:
  - id: get_revision_context
    kind: context_tool
    description: Load session state, artifact lineage, and canonical inputs.
    entrypoint: mozaiksai.control_plane.tools.get_revision_context:get_revision_context
    available_to:
      - request_submitted
      - scope_requested
      - coding_requested

  - id: get_artifact_summary
    kind: context_tool
    description: Load artifact lineage, validation status, and recent change history.
    entrypoint: factory_app.refinement_harness.tools.get_artifact_summary:get_artifact_summary
    available_to:
      - request_submitted
      - scope_requested
      - coding_requested

  - id: get_artifact_workspace_catalog
    kind: context_tool
    description: Load workspace catalog and candidate files for scope selection.
    entrypoint: factory_app.refinement_harness.tools.get_artifact_workspace_catalog:get_artifact_workspace_catalog
    available_to:
      - scope_requested

  - id: get_artifact_workspace_scope
    kind: context_tool
    description: Load workspace tree and related-file previews for scoped coding.
    entrypoint: factory_app.refinement_harness.tools.get_artifact_workspace_scope:get_artifact_workspace_scope
    available_to:
      - coding_requested
```

`get_revision_context` is a framework-provided tool at
`mozaiksai.control_plane.tools.*`. The workspace and artifact tools are
first-party builder tools at `factory_app.refinement_harness.tools.*`.

---

## `refinement_harness/config/policies.yaml` — Optional

Scope size limits and overflow behavior. Omit when defaults are acceptable.

```yaml
schema_version: mozaiks.refinement_harness.policies.v1
scope:
  max_selected_paths: 3
  auto_apply_max_paths: 1
  overflow_behavior: clarify
```

- `max_selected_paths` — maximum file paths allowed in a scoped refinement
- `auto_apply_max_paths` — auto-apply without confirmation at or below this count
- `overflow_behavior` — `clarify` (ask user) or `workflow` (escalate to full workflow)

---

## Prompts

Each LLM-backed checkpoint requires a prompt file. Generated apps should adapt
the factory_app prompts to their domain.

### `prompts/change_classifier_system.yaml`

Minimal starter:

```yaml
id: change_classifier_system
content: |
  You are the authoritative refinement change classifier for <AppName>.

  Classify the request into exactly one of:
  - patch: targeted fix or localized correction within the current artifact boundary
  - design: visual, UX, or schema revision without changing the product concept
  - feature: additive capability within the current product direction
  - core: change in value proposition, target user, product identity, or architecture

  Rules:
  - Use the request text as the primary signal.
  - Use any provided control_plane_context_json as canonical persisted builder state.
  - Treat any user-declared hint as advisory only.
  - Be conservative about core, but choose it when the request changes what the product fundamentally is.
  - Return JSON only. Do not include markdown fences.
  - Keep signals short and semantic.
```

The factory_app `change_classifier_system.yaml` includes staleness-aware routing
rules and artifact family dependency guidance — include those when the app
supports multi-artifact routing.

### `prompts/coding_scope_selection_system.yaml`

```yaml
id: coding_scope_selection_system
content: |
  You are the scope selection agent for <AppName> patch refinements.

  Your job is to pick the narrowest safe file scope for a patch request.

  Rules:
  - Prefer one file when possible.
  - Only choose file paths that appear in the provided workspace catalog.
  - Use the Context Graph catalog when available to identify related files.
  - Resolution options: scoped_files, clarify, workflow.
  - Return JSON only. Do not include markdown fences.
```

### `prompts/coding_refinement_system.yaml`

```yaml
id: coding_refinement_system
content: |
  You are the scoped coding refinement worker for <AppName>.

  Your job is to produce a bounded patch-style refinement against explicit file inputs.

  Rules:
  - Stay scoped to the provided file paths.
  - Return complete updated file content for every changed file in updated_files.
  - Only edit files that appear in the provided explicit file inputs.
  - Use control_plane_context, especially Context Graph scope, to understand nearby files and symbols.
  - Prefer the smallest safe validation strategy.
  - Return JSON only. Do not include markdown fences.
```

---

## Route Rules

- `workflow_sequence` values in `harness.yaml` must exist in
  `workflows/extended_orchestration/extension_registry.json`.
- `affected_declarative_families` and `affected_workflows` belong on the
  sequence in `extension_registry.json`, not in `harness.yaml`.
- Do not declare `requires_replanning` or `requires_rebuild` in route manifests;
  these are derived from the typed change class at runtime.
- `patch` → does not require replanning; eligible for scoped coding worker.
- `design`, `feature`, `core` → require replanning; route to workflow re-entry.

---

## What Not To Generate

Generated refinement harnesses are declarative only:

- no `module.yaml` — the harness is not a module
- no `app/modules/*/backend/refinement_harness*.py` — no custom harness Python
- no business-domain logic in prompts
- no hardcoded model names as prompt content — model config belongs in `refinement_policy.yaml`
- no `affected_workflows` or `affected_families` in `harness.yaml` routes
- no `context_variables.yaml` — the harness is not a workflow
