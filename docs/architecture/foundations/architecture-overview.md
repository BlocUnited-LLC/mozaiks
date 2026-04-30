# Architecture Overview

This is the authoritative architecture reference for Mozaiks. For the detailed
implementation view, see the repo-root `ARCHITECTURE.md`.

## The Model

Mozaiks should be understood like this:

- one AI runtime
- one app backend boundary
- app events
- workflow triggers
- pages on top

That is the main architecture.

If a concept does not help explain one of those four lines, it should not be part of the main reading path.

## What The System Does

Mozaiks is organized around one AI runtime plus one app-host/backend boundary.

In some deployments those may run together. In others they are split services.
App authors should still experience them as one product surface.

Internally, the repo centers the runtime and the app contract:

### Deterministic app behavior (app backend)

This includes:

- users, settings, subscriptions
- notifications
- module execution
- admin APIs
- page backing logic

This behavior is hosted by `mozaiksai/hosts/platform.py` modules by default. Apps may also
choose an external/generated backend that connects through the runtime adapter
boundary.

### Workflow behavior (`mozaiksai/`)

This includes:

- workflow loading
- AG2 orchestration
- run lifecycle
- runtime streaming
- artifacts
- workflow resume and completion

Users should not need to think of these as different products. They are parts
of one application surface.

## The Event Model

When something important happens in normal app behavior, the backend emits an app event.

Examples:

- `set.brief_confirmed`
- `set.direction_selected`
- `set.finalized`
- `subscription.changed`

Events are app facts. They are not workflow names.

Workflow triggers decide whether those events should start or resume a workflow.

The key contract is:

- app event happens
- workflow trigger matches
- workflow runs or resumes

The key workflow trigger declarations live in:

- `app/workflows/{workflow}/orchestrator.yaml` for app-owned workflows
- the shared generation-core workflow root for builder/system workflows

## The Surface Model

### Pages

Pages are the normal app screens that users navigate to.

Examples:

- discover
- archive
- lineup

Admin is a first-class framework surface (like chat-ui), not an app-level page.

### Workflows

Workflows are the agentic runs.

Examples:

- intake
- generation
- review
- rewrite

### Modules

Modules are support bundles behind pages.

They are useful, but they should not be the first mental model for app authors.

## Current Repo Shape

The current repo still carries a bundled starter workspace, but the app-root
contracts are now aligned.

Today it contains:

- `mozaiks-platform/app/*` — current App Zero app root
- `generated/*` and `mozaiks-platform/app-builder/*` — staged generated output
  plus product-owned planning assets
- `chat-ui/` and `web_shell/` — shared shell source plus local shell host

The canonical target is:

- shared generation core outside app workspaces
- self-contained app workspaces with `app/config`, `app/ui/pages`,
  `app/workflows`, `app/modules`, `app/ui`, and `app/brand`
- App Zero converging on that same workspace contract

## What App Authors Should Think About

For most apps, the authoring model should be:

1. `app/app.json`
2. `app/ui/pages/*`
3. `app/workflows/*`
4. `app/modules/*`

`app/modules/*` should be treated as a support layer for shared handlers or
page backing logic, not as the first thing users think about.

## Practical Rule Set

When designing a feature:

1. if it is a screen, make it a page
2. if it is agentic execution, make it a workflow
3. if it is shared backing logic, make it a module
4. if normal app behavior should trigger it, use an app event plus workflow trigger

## What Mozaiks Should Default

Mozaiks should default the things most apps share:

- auth plumbing
- backend URLs
- shell behavior
- mobile defaults
- version defaults
- dev defaults

The user should mostly declare app shape, not platform plumbing.

## Flagship Fit

The `Backstage` flagship should prove this loop:

```text
user action
  -> workflow runs
  -> result is saved
  -> app event happens
  -> workflow trigger follows up if needed
  -> pages reflect the result
```

That is the clearest proof of Mozaiks value.

## Reading Order

Read these first:

1. [Distribution And Workspace Model](distribution-and-workspace-model.md)
2. [Canonical App Structure](canonical-app-structure.md)
3. [Platform Authoring](platform-authoring.md)
4. [Surface Model](surface-model.md)
5. [Event System](event-system.md)
6. [Workflow Architecture](workflow-architecture.md)

Then read:

1. [Core, Product, and App Bundle Boundary](core-product-app-bundle-boundary.md)
2. [Workflow Authoring Contracts](workflow-authoring-contracts.md)
3. App Zero under `mozaiks-platform/app/` when you need the repo-local product
   app bundle

## Builder Product Note

Shared generation-core workflows live in `factory_app/app/workflows/`. App Zero keeps only its product overlay under
`mozaiks-platform/app/workflows/extended_orchestration/extension_registry.json`. Generated apps should
understand the shared workflow contract, not an App Zero-specific workflow
directory layout.


