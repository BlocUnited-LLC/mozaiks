# Control-Plane Manifest

`control_plane/config/control_plane.yaml` declares the app-local harness pack:
artifact routing, LLM-backed checkpoint events, prompt ids, and tool ids.

Routes point to `workflow_sequence` ids only. Workflow
order and impact metadata belong in `workflows/extended_orchestration/extension_registry.json`.

Mozaiks runtime owns the harness implementation, checkpoint handler mapping,
checkpoint mode, and structured output contract. Do not declare Python
entrypoints, handler classes, checkpoint ids, checkpoint modes, or output
contract names in this file.

Example:

```yaml
schema_version: mozaiks.control_plane
routing:
  default_artifact_kind: app_bundle
  artifacts:
    - artifact_kind: app_bundle
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
      - get_stale_artifact_families
  - event: scope_requested
    prompt_id: coding_scope_selection_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary
      - get_artifact_workspace_catalog
```

## Field Selection

Use `routing.default_artifact_kind` for the artifact family most refinement
requests should target when the request does not name a specific artifact. For
normal generated apps this is usually `app_bundle`.

Use `routing.artifacts[].artifact_kind` values from the artifact families your
workflow sequences actually produce, such as:

- `concept`
- `design_docs`
- `workflow_bundle`
- `app_bundle`

For each artifact kind, define all four route classes:

- `patch` for narrow edits.
- `design` for surface/UX/contract reshaping.
- `feature` for additive capability work.
- `core` for concept-level pivots or rebuilds.

Each route value is only a `workflow_sequence` id. That id must exist in
`workflows/extended_orchestration/extension_registry.json`.

Use `checkpoints` only for LLM-backed control-plane events that need
app-specific prompts or tools:

- `request_submitted` classifies the user's change request.
- `scope_requested` selects scoped files or asks for clarification.
- `contract_surface_requested` identifies canonical app/workflow contract
  surfaces for targeted regeneration.
- `coding_requested` produces scoped coding plans.

Do not declare `route_requested` or `decision_requested`. Those are
deterministic runtime checkpoints supplied by Mozaiks.

For each checkpoint, choose:

- `event`: one of the LLM-backed events above.
- `prompt_id`: the id of a file in `control_plane/prompts/*.yaml`.
- `tool_ids`: optional ids from `control_plane/config/tools.yaml`.

Mozaiks infers:

- checkpoint id
- checkpoint mode
- runtime handler implementation
- structured output contract

## Boundaries

Do not put generated Python handlers in an app-local control-plane pack. Use the
canonical runtime handlers supplied by `mozaiksai.control_plane` and declared
context tools.

Do not add these fields:

- `profile`
- `harness`
- `checkpoints[].id`
- `checkpoints[].mode`
- `checkpoints[].entrypoint`
- `checkpoints[].output_contract`

Do not duplicate route impact fields such as `affected_workflows`,
`affected_declarative_families`, `requires_replanning`, `requires_rebuild`, or
`scope_summary` in `control_plane.yaml`. They are derived from workflow sequence
metadata.
