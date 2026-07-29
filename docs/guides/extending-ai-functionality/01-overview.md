# Extending AI Functionality

Mozaiks apps can add AI behavior through workflow bundles and optional
artifact-aware refinement. Most apps start with one workflow and add refinement
only when they need routed revisions.

The key files are:

- `workflows/extended_orchestration/extension_registry.json` declares the
  workflow sequences the refinement engine may re-enter.
- `workflows/{workflow_id}/` declares agents, tools, state, routing, and UI for
  a workflow.
- `app/config/ai.json` starts ask, chat, and workflow behavior.
- `app/config/refinement_policy.yaml` enables routed artifact refinement.
- `refinement_harness/config/harness.yaml` is the app-local overlay that extends
  the packaged default harness; it may be only `overrides: {}` when the default
  routes and checkpoints are sufficient.

When a user asks for a change, the refinement engine can classify whether the
request is a patch, design adjustment, feature addition, or concept-level
pivot. Mozaiks then chooses the smallest valid re-entry point instead of
treating every request as a blind code edit.

Users define semantic policy values: artifact kinds, route classes, workflow
sequence ids, checkpoint events, prompt ids, and tool ids. Mozaiks runtime
defines the harness implementation, deterministic handlers, checkpoint modes,
handler entrypoints, and structured output contracts.

In the canonical app workspace contract, app-local refinement overlay files live
beside the app bundle:

```text
app/
refinement_harness/
workflows/
```

In this repo's first-party builder workspace, the packaged default harness lives
under:

- `factory_app/app/config/ai.json`
- `factory_app/app/config/refinement_policy.yaml`
- `factory_app/refinement_harness/config/harness.yaml`
- `factory_app/workflows/extended_orchestration/extension_registry.json`

## When To Add This

Omit refinement entirely when the app is mostly deterministic modules, CRUD, one
known workflow launch, or fixed workflow sequences.

Add one when the app needs at least two of these signals, or one
governance-critical signal is dominant:

- semantic request intake across multiple valid execution paths
- checkpointed revision or session continuity
- policy, approval, escalation, or risk gating above workflows/modules
- scoped coding or contract-surface planning after route selection
- artifact routing based on request meaning rather than fixed sequence wiring

Ownership is split deliberately:

- `ValueEngine` may hint that a refinement surface is needed.
- `DesignDocs` decides whether `surface_kind = refinement` is warranted.
- `AppGenerator` materializes an app-local refinement overlay for refinement
  surfaces and emits optional tools, policies, or prompts only when the packaged
  default harness cannot express the app-specific delta by itself.
- `AgentGenerator` stays responsible for workflow bundles the refinement engine may route into.

## Artifacts

- [AI Startup](../configs/ai-startup.md)
- [Refinement](../configs/refinement.md)
- [Workflow Sequences](05-workflow-sequences.md)

## Read Next

- [Config Files](../configs/index.md)
- [Context Graph](../platform-intelligence/01-context-graph.md)
- [Harness](../platform-intelligence/02-harness.md)
- [Refinement](../platform-intelligence/03-refinement.md)
