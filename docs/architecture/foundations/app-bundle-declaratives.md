# App Bundle Declaratives

This document defines the declarative families that make up a Mozaiks app
bundle.

The app bundle is the output of planning and generation. It is not the place
where intent is still ambiguous.

The bundle should also be understood relative to the enterprise core:

- core provisions handle recurring SaaS infrastructure concerns
- the bundle configures those provisions where needed
- the bundle declares only the app-specific substrate, automation, workflows,
  and thin stubs that remain

## Core Thesis

Mozaiks should compile user intent into a structured bundle with six declarative
families:

1. app identity
2. shell
3. app substrate
4. modules
5. automation
6. workflows

Each family answers a different question. Mixing them produces weak generators
and blurry runtime boundaries.

## Declarative Families

| Family | Purpose | Canonical target path |
| --- | --- | --- |
| App manifest | App identity and deployment metadata | `platform/app.json` |
| Shell model | Navigation, theme, shell controls, discover | `platform/shell/*` |
| App substrate model | Entities, views, actions, policies | `platform/data/*` |
| Module model | Durable user-facing product areas | `platform/modules/*` |
| Automation model | Domain events and event-to-effect routing | `platform/automations/*` |
| Workflow model | AI reasoning and orchestration | `platform/workflows/*` |

## 1. App Manifest

Owns:

- app identity
- tenant and auth metadata
- endpoint roots
- deployment metadata

Does not own:

- shell structure
- workflow entry logic
- automation routing

## 2. Shell Model

Owns:

- landing behavior
- navigation groups
- semantic header controls
- discover behavior
- theme identity

Does not own:

- entity schemas
- module business logic
- workflow definitions

The shell exists to compose product surfaces, not to define the product model.

## 3. App Substrate Model

Owns the non-AI app contract.

### Entities

Describe durable business objects and relationships.

Examples:

- `Lead`
- `Booking`
- `WriterRoom`
- `EpisodeDraft`

### Views

Describe persistent ways to interact with entities.

Examples:

- list
- detail
- form
- board
- timeline
- dashboard

### Actions

Describe deterministic behavior.

Examples:

- create booking
- update deal stage
- send invoice
- approve submission

If a capability is deterministic and auditable, it should start here, not in a
workflow.

### Policies

Describe access and constraint rules.

Examples:

- role-based access
- plan entitlements
- tenant boundaries
- approval rules

## 4. Module Model

Modules are durable product surfaces built from substrate primitives.

A module may reference:

- one or more views
- one or more actions
- optional workflow entrypoints
- shell placement metadata

Modules should not be the only place where business behavior is defined. They
compose existing declaratives into a user-facing area.

## 5. Automation Model

This is the missing family that ties the app substrate to the AI runtime.

It has two parts:

### Event catalog

Declares the business facts the app emits or consumes.

Examples:

- `crm.lead.created`
- `booking.request.approved`
- `writers_room.brief.updated`
- `settings.updated`

### Routes

Map domain events to automation effects.

Examples:

- run a workflow
- resume a waiting workflow
- create or update an artifact
- notify a user
- no-op

Important rule:

- domain events are facts
- routes are policy
- workflow names appear in routes, not in emitted event types

## 6. Workflow Model

Workflows own:

- reasoning
- handoffs
- HITL pauses
- orchestration
- tool use
- workflow-local UI

Workflow files remain stable and are intentionally not redesigned in this
rewrite.

## What Should Not Be First-Class

These are derived or transitional, not foundational:

- giant `config/` buckets with mixed concerns
- a hand-authored `module_registry.json` as the main source of truth
- per-feature bespoke code as the planning target
- direct coupling from CRUD mutations to workflow names

## Enterprise Core Versus App-Authored Output

The builder should not treat every app concern as generated implementation work.

Use this decision order:

1. already handled by core
2. handled by core but app-configured
3. thin app-authored stub on top of core
4. bespoke app logic

Examples of concerns that should usually land in core or core configuration:

- auth
- tenancy
- notifications
- subscriptions
- shell chrome
- websocket delivery
- automation transport
- workflow runtime

Examples of concerns that should usually remain app-authored:

- domain entities
- domain views and actions
- domain event catalog entries
- automation routes
- domain-specific workflow definitions
- thin integration stubs

## Compiler View

The generator should reason in this order:

```text
Intent
  -> app substrate model
  -> automation model
  -> workflow model
  -> shell and module composition
  -> compiled bundle
```

The runtime then consumes the compiled bundle without needing the original
planning conversation.

## Current Flagship Example

The current `platform/` directory in this repo is the flagship runtime-output
example.

It still uses some transitional runtime projections such as
`platform/config/*.json` and `platform/brand/*`.

Those are valid current outputs, but the builder should still reason in terms
of the six canonical families above and treat the transitional files as compiled
projections of shell or substrate concerns.

## Cross References

- [canonical-app-structure.md](canonical-app-structure.md)
- [app-creation-guide.md](app-creation-guide.md)
- [workflow-architecture.md](workflow-architecture.md)
