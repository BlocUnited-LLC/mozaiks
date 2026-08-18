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
<!-- END MOZAIKS MANAGED: agent-guidance -->
