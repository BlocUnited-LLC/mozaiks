# Module Authoring Patterns

This document captures common deterministic module implementation patterns.

These are authoring patterns, not runtime type declarations.
They do not change the canonical module contract described in
[Module System](module-system.md).

## Core Rule

Every module still uses the same contract:

- `module.yaml`
- optional `contracts/`
- `backend/handler.py`
- optional backend support files

Patterns change implementation emphasis, not file ownership.

## Common Patterns

### Registry and CRUD pattern

Use for:

- app registries
- listings
- profiles
- preference-backed records
- simple admin-managed datasets


The page surface still lives under `app/ui/pages/`.
The workflow layer remains separate.

### Audit and activity pattern

Use for:

- audit trails
- activity feeds
- immutable event records
- moderation or review histories
- operational logs that need queryable retention

Keep append-only record creation in `service.py`, query access in `repo.py`,
scope helpers in `policy.py`, and typed activity shapes in `schemas.py`.

### External adapter pattern

Use for:

- third-party webhook intake
- outbound service clients
- wrappers around existing external systems

The external system remains external.

### Hosted-pack facade pattern

Use for host-provided capabilities that should appear in generated apps without
copying the hosted service internals into the generated app.

The generic shape is:

```text
hosted_pack
  -> backend/integrations/{pack_id}_client.py
  -> app-owned facade module
  -> ui/pages bind to the facade module
```

Provider-neutral example:

```text
hosted_analytics
  -> backend/integrations/hosted_analytics_client.py
  -> modules/analytics_dashboard/
  -> ui/pages/analytics.yaml
  -> /api/modules/analytics_dashboard/get_metrics
```

Pages must call the app-owned facade module API. They must not bind directly to
hosted-pack internals or assume hosted service routes exist inside the generated app.

## Backend Helper-File Governance

Helper files are allowed only when all of these are true:

- declared before generation in owned paths or generated Python stubs
- module-local under `backend/`
- justified by a specific purpose
- imported by canonical layers or referenced by `runtime_extensions.yaml`

Allowed generic examples:

- external provider client
- webhook receiver helper
- startup service helper
- audit event subscriber
- notification delivery client
- complex pure domain helper that would bloat `service.py`

Prohibited uses:

- generic business logic that belongs in `service.py`
- persistence/query access that belongs in `repo.py`
- authorization or scope logic that belongs in `policy.py`
- DTOs, typed shapes, or pure helpers that belong in `schemas.py`
- transport or WebSocket infrastructure
- workflow orchestration
- random file splitting

## What Not To Do

- Do not model workflows as modules.
- Do not put persistent pages in module backend directories.
- Do not use legacy transport companion files as canonical module authoring outputs.
- Do not use legacy state-machine companion files as canonical module files.
- Use `schemas.py` as the canonical typed-shape file.
- Do not use helper files as a new default layer. Only add a helper file when
  it is explicitly justified, module-local, and imported by a canonical backend
  layer or referenced by `runtime_extensions.yaml`.
- Do not use fake/sample/demo data or hardcoded KPI percentages as runtime
  module output. Summary, stats, metrics, and count actions must query repo/DB
  state or return honest empty values with null trends.

## Cross References

- [Module System](module-system.md)
- [Capability Pack Model](capability-pack-model.md)
- [Platform Authoring](../app/platform-authoring.md)
