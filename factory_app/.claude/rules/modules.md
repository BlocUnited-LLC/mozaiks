---
paths:
  - "app/modules/**"
---

<!-- BEGIN MOZAIKS MANAGED: agent-guidance -->
# Module Rules

Modules are deterministic app capabilities.

Canonical module shape:

```text
app/modules/{module_id}/
  module.yaml
  runtime_extensions.yaml        # optional
  contracts/                     # optional companion manifests
  backend/
    handler.py
    service.py                   # recommended for business logic
    repo.py                      # recommended for data access
    policy.py                    # recommended for multi-tenant scoping
    schemas.py                   # recommended for typed payloads/docs
```

## Rules

- `module.yaml` declares actions and capabilities.
- `backend/handler.py` stays thin: validate/dispatch/return only.
- Business logic belongs in `service.py`.
- MongoDB/data access belongs in `repo.py`.
- Tenant/user scoping belongs in `policy.py`.
- Typed payloads and document shapes belong in `schemas.py`.
- Publish domain events through declared contracts; do not hardcode workflow starts in module code.
- Use `runtime_extensions.yaml` for API routers or startup services only when the module needs them.

## Cross-Module Composition

Two sanctioned mechanisms exist for one module using another. Choose by
this rule, not by mimicking the nearest neighbor:

- **Events (`contracts/reactions.yaml`)** — for reacting to *state changes*.
  When module A changes state that module B cares about, A emits a declared
  event and B subscribes with a reaction. This is the only sanctioned way a
  module triggers behavior in another module. Reaction targets of
  `kind: handler` must name a method that actually exists on the module's
  handler class.
- **Direct service import** — for *synchronous reads only*. A module may
  import another module's Service class (deferred import to avoid cycles),
  instantiate it, and call a read method with the caller's ctx, wrapped so a
  failure degrades instead of breaking the caller's own read path.

Never direct-import another module's service to perform a **write** — writes
cross module boundaries only through declared events, or through an explicit
bridge the owning module publishes for that purpose (with its own
permission-wrapping context and idempotency). Never import another module's
repo, and never touch another module's collections directly.
<!-- END MOZAIKS MANAGED: agent-guidance -->
