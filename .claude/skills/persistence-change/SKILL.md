---
name: persistence-change
description: Review or implement a change to database_intent, database_migrations, repo.py generation rules, ModuleContext.persistence, or persistence guidance.
argument-hint: "[change summary or file path]"
---

Use this skill when a change touches persistence contracts or generated module
repo boundaries.

Inspect first:

- `AGENTS.md`
- `CLAUDE.md`
- `docs/architecture/builder/database-intent-and-revision-contract.md`
- `docs/architecture/foundations/events-and-data/persistence-and-artifact-storage.md`
- `factory_app/workflows/AppGenerator/tools/file_contracts.yaml` when generator
  file families or backend defaults are involved

Current persistence truth:

- `database_intent_bundle` is the canonical planning object
- `config/database_intent.json` is the exported app artifact
- `config/database_migrations/{migration_id}.json` is the additive migration artifact
- generated module repo code uses `ModuleContext.persistence` as `ctx.persistence`
- `ctx.db` is absent and non-canonical
- `backend/repo.py`, `backend/policy.py`, and `backend/schemas.py` are the
  canonical persistence support files

Do not reintroduce:

- `backend/models.py`
- `backend/models/*.py`
- `backend/database/schema.json`
- `backend/database/seed.json`
- raw persistence logic in `handler.py` or `service.py`

Return:

1. Layer changed
2. Runtime/platform impact
3. App workspace impact
4. Persistence artifacts affected
5. Tests required/run
6. Compatibility risk