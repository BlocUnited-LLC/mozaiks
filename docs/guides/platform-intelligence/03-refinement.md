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

The classifier is backed by `autogen.beta.Agent.ask()` with a `response_schema`
that enforces the `ChangeClassifierResult` shape at the provider level. It reads
persisted builder state from the control-plane context tools before calling the
LLM, so it can take staleness into account:

- if an upstream artifact family is stale, the classifier upgrades the change
  class to ensure the stale family is refreshed along the chosen route
- if the request is clearly bounded to current files, it classifies conservatively

## How Patch Coding Works

For `patch` requests, the control plane runs a scoped coding worker backed by
`ScopedRefinementCodingWorker` (also using `agent.ask()`). The coding worker:

1. proposes file scope from the workspace catalog if none is provided
2. generates complete updated file content for the scoped files only
3. validates the output and persists a new artifact version

The coding worker is intentionally conservative — it only edits files explicitly
in scope and escalates to a full workflow if the request is broader than a patch.

## For Builders: Opting In

An app opts into the control plane through its `app/config/ai.json`:

```json
{
  "control_plane": {
    "enabled": true,
    "classifier": { "enabled": true },
    "coding": { "enabled": true }
  }
}
```

LLM profiles and checkpoint declarations live in `control_plane/config/`. See
[App-Local Control Plane Pack](../../architecture/app/control-plane-pack.md)
for the full generated app starter pack template.

For the full runtime behavior, see [Refinement Control Plane](../../architecture/workflows/refinement-control-plane.md).
