# UX Route Normalization Spec

**Status:** Working specification
**Purpose:** Define the canonical customer-facing route model.
**Depends on:** [../foundations/platform-terminology-and-brand-language.md](../foundations/platform-terminology-and-brand-language.md), [../foundations/platform-information-architecture.md](../foundations/platform-information-architecture.md), [../foundations/generated-app-lifecycle-model.md](../foundations/generated-app-lifecycle-model.md), [../foundations/admin-system.md](../foundations/admin-system.md)

## Why This Exists

The route model must follow the customer-facing IA directly.

This spec exists so route and navigation work uses one canonical vocabulary:
`Apps`, `Build`, `Deploy`, `Usage`, `Operations`, `Integrations`, `Users`,
`Settings`, and `Admin`.

## Target Customer-Facing Route Families

### Workspace-Level

```text
/apps
/usage
/operations
/billing
/settings
```

### App-Level

```text
/apps/:appId/overview
/apps/:appId/build
/apps/:appId/deploy
/apps/:appId/usage
/apps/:appId/operations
/apps/:appId/integrations
/apps/:appId/users
/apps/:appId/settings
/apps/:appId/admin
```

## Entry Rules

- The default customer-facing landing area should become `/apps`.
- `Create App` should create a draft app record immediately.
- After creation, the user should be routed directly to
  `/apps/:appId/build`.
- The user should not remain in a free-floating create chat with no app record.

## Canonical Product Route Families

New product surfaces must live under these route families:

- `/apps`
- `/apps/:appId/*`

## Admin Route Rule

`Admin` is part of the App Console.

The first-party app console uses:

```text
/apps/:appId/admin
/apps/:appId/users
/apps/:appId/usage
/apps/:appId/operations
/apps/:appId/settings
```

The framework-owned admin shell still renders inside that app context. Product
documentation should not describe a separate top-level `/admin` area or a
separate customer-facing admin product.

## Workspace Readiness Redistribution

The current workspace-readiness content does not justify a standalone product
area.

Redistribute it as follows:

- workspace readiness and "what next" copy -> `Apps` empty states and app
  creation entry
- provider/model defaults -> `Settings`
- workspace diagnostics -> `Settings` or a future diagnostics subsection
- current build entry -> app-specific `Build`

The visible product does not use a top-level `Studio` route.

## Build Route Rules

`Build` should be app-scoped.

Implications:

- initial create flow becomes one way to enter `Build`
- revision flow also re-enters `Build`
- build history and artifact review belong to the app context
- the user should always feel they are evolving one app, not entering a generic
  builder area detached from the app lifecycle

## Integrations Route Rules

Use `Integrations` as the visible term.

Rules:

- app-owned integrations should live under `/apps/:appId/integrations`
- workspace-owned credentials or shared provider defaults may later live under
  `/settings`
- avoid visible `Adapters` terminology

## Operations Route Rules

Use `Operations` for:

- incidents
- failures
- runtime health
- deployment health
- workflow or orchestration problems when surfaced to users

Use `Usage` for:

- tokens
- spend
- API volume
- counts and consumption metrics

Do not overload `Activity` to mean both history and health.

## Implementation Rule

- register customer-facing routes only under the canonical families in this spec
- do not add new product pages under `/hub` or `/studio/*`
- treat historical route names as removed from product IA

## Terms Removed From Routes

These should disappear from future customer-facing route families:

- `hub`
- `studio`
- `adapters`

Preferred visible replacements:

- `apps`
- `build`
- `integrations`
