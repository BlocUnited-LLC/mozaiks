# UX Route Normalization Spec

**Status:** Working specification
**Purpose:** Define the canonical customer-facing route model.
**Depends on:** [../foundations/platform-terminology-and-brand-language.md](../foundations/platform-terminology-and-brand-language.md), [../foundations/platform-information-architecture.md](../foundations/platform-information-architecture.md), [../foundations/generated-app-lifecycle-model.md](../foundations/generated-app-lifecycle-model.md), [../foundations/admin-system.md](../foundations/admin-system.md)

## Why This Exists

The route model must follow the customer-facing IA directly.

This spec exists so route and navigation work uses one canonical vocabulary:
`Apps`, `Overview`, `Usage`, `Billing`, `Hosting`, `Integrations`, and `Users`.

## Target Customer-Facing Route Families

### Workspace-Level

```text
/apps
/usage
/billing
/hosting
```

### App-Level

```text
/apps/:appId/overview
/apps/:appId/users
/apps/:appId/usage
/apps/:appId/billing
/apps/:appId/hosting
/apps/:appId/integrations
```

## Entry Rules

- The default customer-facing landing area should become `/apps`.
- `Create App` should create a draft app record immediately.
- After creation, the user should be routed directly to
  `/apps/:appId/overview`.
- The user should not remain in a free-floating create chat with no app record.

## Canonical Product Route Families

New product surfaces must live under these route families:

- `/apps`
- `/apps/:appId/*`

## Workspace Readiness Redistribution

The current workspace-readiness content does not justify a standalone product
area.

Redistribute it as follows:

- workspace readiness and "what next" copy -> `Apps` empty states and app
  creation entry
- finance posture -> `Billing`
- hosting posture -> `Hosting`
- current workflow entry -> workflow-owned create/refinement paths, not a
  persistent console route

The visible product does not use a top-level `Studio` route.

## Billing Route Rules

Use `Billing` for:

- revenue
- recurring value
- payment posture
- commercial readiness

Do not merge Billing into Hosting.

## Hosting Route Rules

Use `Hosting` for:

- domains
- environment posture
- managed rollout readiness
- production handoff state

Do not merge Hosting back into Billing or present it as `Deploy`.

## Integrations Route Rules

Use `Integrations` as the visible term.

Rules:

- app-owned integrations should live under `/apps/:appId/integrations`
- workspace-owned credentials or shared provider defaults may later live under
  `/settings`
- avoid visible `Adapters` terminology

Use `Usage` for:

- tokens
- spend
- API volume
- counts and consumption metrics

Do not add unfinished `Operations`, `Settings`, or `Admin` pages to the
production console route model.

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
- `billing`
- `hosting`
- `integrations`
