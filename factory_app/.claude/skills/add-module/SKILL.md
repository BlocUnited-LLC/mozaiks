---
name: add-module
description: Add or update a deterministic Mozaiks app module in this generated app workspace.
argument-hint: "[module goal]"
disable-model-invocation: true
---

<!-- BEGIN MOZAIKS MANAGED: agent-guidance -->
Complete this module task: $ARGUMENTS

1. Read `AGENTS.md` and `.claude/rules/modules.md`.
2. Create or update `app/modules/<module_id>/module.yaml`.
3. Keep `backend/handler.py` thin.
4. Put business logic in `backend/service.py`.
5. Put persistence in `backend/repo.py`.
6. Add `backend/policy.py` and `backend/schemas.py` when the module reads/writes scoped data.
7. Add `runtime_extensions.yaml` only for declared API routers or startup services.
8. Update docs and `.env.example` for new setup requirements.
<!-- END MOZAIKS MANAGED: agent-guidance -->
