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
first — it covers the ownership model, harness model, and AG2 implementation
details that this document builds on.

---

## When An App Needs A Refinement Harness

Most generated apps do **not** need an app-local refinement harness. Default to
ordinary workflow launches, module actions, and `extension_registry.json`
workflow sequences.

Add an app-local harness only when the generated app explicitly needs:

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
    refinement_policy.yaml required — LLM profiles and capability feature flags
refinement_harness/
  config/
    harness.yaml    required — app overlay over mozaiks.default_refinement_harness
    tools.yaml      optional — app-specific tool deltas only
    policies.yaml   optional — app-specific policy deltas only
  prompts/
    *.yaml          optional — app-specific prompt overrides only
```

The `app/config/ai.json` file owns ask/chat/workflow startup. The LLM profile
policy lives beside it in `app/config/refinement_policy.yaml`; both files are
separate from the `refinement_harness/` directory.

---

## `app/config/ai.json` — Startup Boundary

Startup config does not declare refinement routing, checkpoints, prompt content,
or policy. Keep ask/chat/workflow startup in `app/config/ai.json`; put
refinement model policy in `app/config/refinement_policy.yaml`; put refinement
routes and checkpoint overrides under `refinement_harness/`.

```json
{
  "chat": {
    "chat_startup_mode": "ask"
  },
  "workflows": {
    "entry_point": "ValueEngine"
  }
}
```

---

## `app/config/refinement_policy.yaml` — Minimal Starter

Declares LLM profiles for each capability. The classifier and codegen profiles
are the two required for refinement-capable apps.

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

Add `contract_surface` when `contract_surface_requested` is included:

```yaml
  contract_surface:
    enabled: true
    llm_profile: codegen
```

---

## `refinement_harness/config/harness.yaml` — Default Overlay

The minimal generated harness consumes the packaged OSS defaults:

```yaml
schema_version: mozaiks.refinement_harness.v1
extends: mozaiks.default_refinement_harness
overrides: {}
```

The default OSS harness supplies routing for the standard artifact families,
the checkpoint chain, deterministic handlers, tools, policies, and prompt text.
The `workflow_sequence` ids used by the effective harness must exist in the
effective `workflows/extended_orchestration/extension_registry.json`.

### Adding Contract Surface Planning

The default harness includes contract surface planning. Only add a local
checkpoint override when an app needs a different prompt or a real app-specific
tool delta:

```yaml
overrides:
  checkpoints:
  - event: contract_surface_requested
    prompt_id: contract_surface_selection_system
    tool_ids:
      - get_contract_surface_context
      - get_app_intelligence_context
```

And add `contract_surface` to `refinement_policy.yaml` as shown above.

Prompt overrides can be declared inline or by path:

```yaml
overrides:
  prompts:
    contract_surface_selection_system: refinement_harness/prompts/contract_surface_selection_system.yaml
```

The override file uses the same prompt shape:

```yaml
id: contract_surface_selection_system
content: |
  You are the contract surface planner for <AppName>.
```

Do not copy the default prompt when the default behavior is sufficient.

### Multi-Artifact Routing

For apps with non-default artifact kinds or product-specific route changes, add
only the changed artifact entries under `overrides.routing.artifacts`. Each
artifact kind routes independently:

```yaml
overrides:
  routing:
    artifacts:
      - artifact_kind: custom_report
        label: custom report
        routes:
          patch:
            workflow_sequence: custom_report_patch
          design:
            workflow_sequence: custom_report_revision
          feature:
            workflow_sequence: custom_report_revision
          core:
            workflow_sequence: full_rebuild
```

---

## `refinement_harness/config/tools.yaml` — Optional Tool Delta

Default context tools come from `factory_app/refinement_harness/config/tools.yaml`.
Create an app-local `tools.yaml` only when the app declares an app-specific
context tool, and include only the delta:

```yaml
schema_version: mozaiks.refinement_harness.tools.v1
tools:
  - id: get_domain_context
    kind: context_tool
    description: Load app-specific domain facts needed by a refinement checkpoint.
    entrypoint: app.services.refinement_context:get_domain_context
    available_to:
      - scope_requested
      - coding_requested
```

Do not copy the default OSS tools file into an app workspace.

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

LLM-backed checkpoints use the default prompt text from
`factory_app/refinement_harness/prompts/`. Generated apps should not copy those
files. Add app-local prompt files only when product semantics genuinely require
different instructions, then reference the override from `harness.yaml`.

### `prompts/change_classifier_system.yaml`

Example override:

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
  - Use any provided refinement_context_json as canonical persisted builder state.
  - Treat any user-declared hint as advisory only.
  - Be conservative about core, but choose it when the request changes what the product fundamentally is.
  - Return JSON only. Do not include markdown fences.
  - Keep signals short and semantic.
```

The factory_app `change_classifier_system.yaml` already includes staleness-aware
routing rules and artifact family dependency guidance.

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
  - Use refinement_context, especially Context Graph scope, to understand nearby files and symbols.
  - Prefer the smallest safe validation strategy.
  - Return JSON only. Do not include markdown fences.
```

---

## Route Rules

- `workflow_sequence` values in `harness.yaml` must exist in
  `workflows/extended_orchestration/extension_registry.json`.
- Apps that consume default factory sequences should set
  `"extends": "mozaiks.default_workflow_registry"` in the workflow registry and
  declare only app-local sequence, transition, workflow, or entrypoint deltas.
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
- no `app/modules/*/backend/control_plane*.py` — no custom harness Python
- no business-domain logic in prompts
- no hardcoded model names as prompt content — model config belongs in `refinement_policy.yaml`
- no copied default `tools.yaml`, `policies.yaml`, or prompt files
- no `affected_workflows` or `affected_families` in `harness.yaml` routes
- no `context_variables.yaml` — the harness is not a workflow
