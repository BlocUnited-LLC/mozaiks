# Generated App Lifecycle Model

Generated apps need a persistent product lifecycle that is separate from
individual workflow runs or chat sessions.

The app should exist as a managed product record from the first create action
forward. Users should not be trapped in a transient conversation before an app
appears in the product.

## Lifecycle Principles

- Create a persistent app record immediately.
- Treat workflow runs as execution details, not the app lifecycle itself.
- Allow incomplete apps to exist visibly in Workspace Studio.
- Keep `Build` as the primary surface for incomplete or revising apps.
- Preserve context and history across revisions.
- Let downstream surfaces become available progressively as the app matures.

## Canonical States

| State | Meaning | Primary CTA | Visible Surfaces |
| --- | --- | --- | --- |
| `draft` | app record exists, but no meaningful build has completed yet | `Continue Build` | Apps, Overview, Build |
| `building` | workflows are actively producing or revising outputs | `Open Build` | Apps, Overview, Build |
| `review` | generated outputs are ready for approval, comparison, or revision choice | `Review Changes` | Apps, Overview, Build |
| `configuring` | app needs integrations, hosting, permissions, or environment setup | `Configure App` | Overview, Build, Deploy, Integrations, Settings |
| `deployed` | deployment completed, app is installed or published but may still be stabilizing | `Open Overview` | All app-studio sections |
| `active` | app is live and operating normally | `Open Overview` | All app-studio sections |
| `needs_revision` | an upstream change, validation failure, or user request requires follow-up | `Resume Build` | Apps, Overview, Build, Operations |
| `archived` | app is intentionally inactive but retained | `View Details` | Overview, Settings |

## State Meanings

### Draft

Use `draft` as soon as the user creates an app or submits the first request.

This allows:

- a visible app card in `Apps`
- a stable destination for subsequent work
- saved intent and lifecycle metadata
- draft empty states in Overview and Build

### Building

Use `building` when workflows, revisions, or build jobs are actively in
progress.

The key rule is that the user still manages an app, not a free-floating chat.

### Review

Use `review` when outputs exist and Mozaiks needs a human to compare, approve,
or choose a path before continuing.

Examples:

- build artifact ready for approval
- refinement produced candidate changes
- deployment bundle available for review

### Configuring

Use `configuring` after core outputs exist but before the app is ready to run as
a managed product.

Examples:

- credentials missing
- integration setup incomplete
- billing or hosting steps required
- environments not configured

### Deployed And Active

`deployed` marks successful release completion.

`active` marks that the app is now operating as a live managed product, with
usage, operations, and admin concerns becoming primary.

The exact transition from `deployed` to `active` may depend on product-specific
criteria, but the UX distinction is useful:

- `deployed` = release event
- `active` = steady-state operating app

### Needs Revision

Use `needs_revision` when the app is no longer aligned with accepted intent or
validated outputs.

Examples:

- a core upstream change invalidated downstream outputs
- a deployment or validation check failed
- a user requested a meaningful revision after prior outputs existed

This state should guide the user back into `Build`, not into a raw workflow
concept.

## Lifecycle Transitions

Typical path:

```text
draft
  -> building
  -> review
  -> configuring
  -> deployed
  -> active
```

Revision path:

```text
active or deployed
  -> needs_revision
  -> building
  -> review
  -> configuring or active
```

Archival path:

```text
draft | active | deployed | needs_revision
  -> archived
```

## UX Behavior By State

### In The Apps Directory

- `draft`: show the app with a `Continue Build` action
- `building`: show progress or in-flight status
- `review`: show that attention is required
- `configuring`: show setup-dependent status
- `deployed` and `active`: show as ready
- `needs_revision`: show warning state and route back to Build
- `archived`: de-emphasize in default lists

### In App Studio

- `Overview` should always exist, even for `draft`
- `Build` is primary for `draft`, `building`, `review`, and `needs_revision`
- `Deploy` and `Integrations` become primary during `configuring`
- `Usage`, `Operations`, and `Admin` become primary during `deployed` and `active`

### Empty And Partial States

Do not hide sections just because the app is incomplete.

Instead:

- show lifecycle-aware placeholders
- explain what the user needs to do next
- link directly into the section that can advance the app

This is cleaner than forcing the user to stay in a single workflow or chat until
completion.

## Relationship To Workflows And Runtime

The app lifecycle is not the same thing as:

- workflow status
- session lifecycle
- workflow sequence status
- artifact lifecycle

Those runtime states still matter, but they support the app lifecycle rather
than replacing it.

Examples:

- a workflow run can fail while the app remains `building`
- a revision request can move the app to `needs_revision` even when the last run
  completed successfully
- an artifact can enter review while the app remains in `review` or
  `configuring`

## Relationship To Revision Routing

The runtime harness may decide:

- continue the current build context
- reopen an upstream workflow in revision mode
- patch a local artifact

That routing decision should affect the app lifecycle, but the user should still
see product terms:

- `Resume Build`
- `Review Changes`
- `Needs Revision`

Not:

- `workflow_sequence restart`
- `control-plane reroute`
- `groupchat resume`
