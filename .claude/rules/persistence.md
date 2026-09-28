---
paths:
  - "AGENTS.md"
  - "CLAUDE.md"
  - "docs/**/*.md"
  - ".claude/**/*.md"
  - "app/modules/**"
  - "factory_app/workflows/**"
---

# Persistence Rules

Use these rules when changing persistence contracts, generated persistence
artifacts, or module persistence boundaries.

## Canonical Persistence Artifacts

- `data_contract` is the canonical planning object.
- Generated app bundles persist it as `data/contract.json`.
- Additive revisions belong under
  `data/migrations/{migration_id}.json`.

## Module Persistence Boundary

- The runtime injects `ModuleContext.persistence` as `ctx.persistence`, not
  `ctx.db`.
- `backend/repo.py` owns persistence operations only.
- Runtime persistence enforces declared collection ownership from the authenticated
  principal; `backend/policy.py` supplies optional preflight helpers.
- `backend/schemas.py` owns typed document/request shapes and pure helpers.
- `backend/service.py` coordinates repo calls, business logic, and event
  emission.
- `backend/handler.py` stays thin dispatch only.

## Do Not Reintroduce

- `backend/models.py`
- `backend/models/*.py`
- `backend/database/schema.json`
- `backend/database/seed.json`
- generated module code that requires or emits `ctx.db`

Generated repo code should use:

- `ctx.persistence.collection(module_id, collection_name)`

Use the declared collection `name`; a declared `entity` resolves to the same
physical collection and ownership policy. Unknown references fail closed.

## Change Discipline

- When persistence contracts change, update runtime docs, tests, and any
  factory workflow prompts or file catalogs that emit persistence artifacts.
- Do not describe speculative persistence APIs as current.
- Keep repo boundaries clean: persistence in `repo.py`, not in `handler.py` or
  raw CRUD branches inside `service.py`.
