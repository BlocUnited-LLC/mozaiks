# Refinement

Refinement is how Mozaiks changes an app after the first build.

Initial generation creates the app. Refinement keeps it alive and editable.
When a user asks for a change, Mozaiks classifies the request and then chooses
the smallest safe next step.

## The Four Change Classes

Mozaiks classifies every refinement request as one of four classes:

- `patch` - a small, localized fix
- `design` - a visual or structural change without a new capability
- `feature` - a new capability within the same product direction
- `core` - a concept-level pivot

## What Each Class Means

- `patch` usually means a scoped code edit against the affected files only
- `design` usually means regenerating page or layout surfaces while keeping the
  backend intact
- `feature` usually means re-entering a workflow sequence with updated planning
  context
- `core` usually means restarting from concept and value planning

## Why This Matters

The point of refinement is to avoid doing more work than the change requires.
Mozaiks preserves the current build state, targets the relevant surfaces, and
keeps the rest of the app stable.

## How Classification Works

The classifier is backed by `ag2.Agent.ask()` with a `response_schema`
that enforces the `ChangeClassifierResult` shape at the provider level. It reads
persisted builder state from the refinement context tools before calling the
LLM, so it can take staleness into account:

- if an upstream artifact family is stale, the classifier upgrades the change
  class to ensure the stale family is refreshed along the chosen route
- if the request is clearly bounded to current files, it classifies conservatively

## How Patch Coding Works

For `patch` requests, the Refinement Engine runs a scoped coding worker backed by
`ScopedRefinementCodingWorker` (also using `agent.ask()`). The coding worker:

1. proposes file scope from the workspace catalog if none is provided
2. generates complete updated file content for the scoped files only
3. validates the output and persists a new artifact version

The coding worker is intentionally conservative — it only edits files explicitly
in scope and escalates to a full workflow if the request is broader than a patch.

## For Builders: Opting In

An app opts into app-local refinement through `app/config/refinement_policy.yaml`:

```yaml
schema_version: mozaiks.refinement.policy.v1
enabled: true
classifier:
  enabled: true
coding:
  enabled: true
```

LLM profiles live in `app/config/refinement_policy.yaml`. Checkpoint and routing
declarations live in `refinement_harness/config/harness.yaml`. See
[App-Local Refinement Harness](../../architecture/app/refinement-harness.md)
for the full generated app starter pack template.

For the full runtime behavior, see [Refinement Engine](../../architecture/workflows/refinement-engine.md).
