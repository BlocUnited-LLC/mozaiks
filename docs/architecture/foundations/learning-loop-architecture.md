# Learning Loop Architecture

The learning loop is the feedback path from runtime observations back into
better workflow, UI, and app-bundle authoring decisions.

It is not a separate app feature pack. It is a framework concern that should be
built from existing runtime signals, artifacts, sessions, and admin surfaces.

## Inputs

Learning-loop inputs can include:

- workflow run metadata
- structured outputs
- tool-call results
- UI tool events
- user feedback and refinement requests
- generated artifacts and patchsets
- runtime errors and validation failures

## Outputs

Learning-loop outputs should improve the authoring and operating experience:

- clearer validation errors
- better workflow prompts and contracts
- safer generated app-bundle patches
- admin-visible run summaries
- refinement suggestions for existing artifacts

## Boundaries

- Do not put learning heuristics inside low-level tools.
- Do not make workflow tools infer product strategy from keywords.
- Do not create a second hidden source of truth for workflow or app state.
- Keep persisted observations tied to `app_id`, `user_id`, `chat_id`, and
  workflow identity where those scopes are available.

## Related Docs

- [Runtime State and Control Events](runtime-state-and-control-events.md)
- [Event System Architecture](event-system-architecture.md)
- [Workflow Authoring Contracts](workflow-authoring-contracts.md)
