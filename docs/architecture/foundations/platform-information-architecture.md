# Platform Information Architecture

This document defines the customer-facing product hierarchy for Mozaiks and
separates it from runtime infrastructure and internal engineering composition.

## Core Model

Mozaiks has three user-visible layers and one internal infrastructure layer.

1. `Mozaiks`
   - the overall product
2. `Workspace Studio`
   - the multi-app account or tenant management surface
3. `App Studio`
   - the single-app management surface
4. internal runtime systems
   - workflows, sessions, artifacts, Refinement Engine, and orchestration state

The user should understand the first three. The fourth layer should rarely be
named in product UX.

## Product Boundary Definitions

### Mozaiks

The root platform product.

Owns:

- authentication and account entry
- workspace-level shell
- app catalog
- hosted product relationship when provided by a host app
- cross-app usage and operational visibility

Does not own as a visible concept:

- factory-specific implementation terms
- workflow execution internals
- Refinement Engine routing details

### Workspace Studio

The multi-app management surface for one workspace or tenant.

Owns:

- list of owned apps
- create-app entry
- aggregate usage
- aggregate operations and health
- host-provided billing or commercial controls when the host exposes them
- workspace-level settings and team controls

### App Studio

The single-app management surface entered after selecting an app.

Owns:

- app overview
- build and revision flow
- deploy and environment state
- app usage and operations
- integrations
- users
- settings
- admin access

### Internal Runtime Systems

These are required to make the product work, but they are not user-facing
product areas.

Includes:

- workflows
- workflow sequences
- session router
- artifact store
- Refinement Engine
- refinement router
- runtime transports and event dispatch

## Navigation Hierarchy

### Workspace-Level Navigation

Recommended top-level sections:

- `Apps`
- `Usage`
- `Operations`
- `Settings`

Definitions:

- `Apps` is the default landing area and multi-app directory.
- `Usage` is cross-app consumption and spend.
- `Operations` is cross-app health, incidents, failures, and runtime status.
- Host apps may add billing or commercial controls as proprietary workspace
  surfaces. They are not required OSS factory Studio routes.
- `Settings` is workspace, team, permissions, and defaults.

`Workspace` is the context label, not the primary nav title.

### App-Level Navigation

Recommended app Studio sections:

- `Overview`
- `Build`
- `Deploy`
- `Usage`
- `Operations`
- `Integrations`
- `Users`
- `Settings`
- `Admin`

Definitions:

- `Overview` summarizes app status, lifecycle state, and major actions.
- `Build` owns create, revise, review, and workflow-driven app evolution.
- `Deploy` owns release state, environments, hosting, and rollout.
- `Usage` owns app-specific tokens, costs, API volume, and product metrics.
- `Operations` owns incidents, runtime health, validation failures, and alerts.
- `Integrations` owns connected external systems and service configuration.
- `Users` owns app-level user and tenant management.
- `Settings` owns app-level configuration and defaults.
- `Admin` owns privileged admin panels and operator/business controls.

## Build And Admin Separation

`Build` and `Admin` must remain separate.

`Build` is:

- app creation
- revision and refinement
- artifact review
- workflow-guided evolution

`Admin` is:

- privileged app management
- runtime/admin panels
- business/admin panels
- feature/module admin panels

This matches the existing admin-system contract: the admin shell is a separate
framework-owned surface and should not collapse into the build flow.

## Relationship To Current Implementation

The current repo already hints at this separation, but the naming is still
mixed:

- `mozaiksai.hosts.studio` is the management host composition
- `factory_app/app/` is the first-party app bundle loaded by that host
- `factory_app/workflows/` is the shared builder workflow root
- `factory_app/refinement_harness/` is the first-party harness pack
- `mozaiksai/control_plane/` is runtime orchestration infrastructure

The IA goal is not to expose those boundaries directly. The user-visible model
should be simpler:

```text
Mozaiks
  -> Workspace Studio
     -> Apps
     -> Usage
     -> Operations
     -> Settings

  -> App Studio
     -> Overview
     -> Build
     -> Deploy
     -> Usage
     -> Operations
     -> Integrations
     -> Users
     -> Settings
     -> Admin
```

## Surface Ownership

| Surface | User-visible role | Internal owner |
| --- | --- | --- |
| Workspace Studio | multi-app management | Studio host + first-party app bundle today |
| Build | creation and revision experience | first-party builder workflows + runtime harness |
| Admin | privileged app management | framework-owned admin shell |
| App pages | generated or app-authored product UI | app workspace |
| Refinement Engine | hidden orchestration layer | runtime infrastructure |

## Terms That Should Disappear From IA

These should not appear as top-level product areas:

- `Hub`
- `Refinement Engine`
- `Factory App`

## UX Movement Model

The standard user path should be:

1. enter Mozaiks
2. land in `Apps`
3. create a new app or choose an existing one
4. enter that app's `Build` or `Overview` surface depending on lifecycle state
5. move into `Deploy`, `Operations`, `Usage`, or `Admin` as the app matures

The system should feel like one coherent platform:

- Workspace Studio manages many apps
- App Studio manages one app
- Build evolves the app
- Admin governs the app
- runtime orchestration stays behind the scenes


