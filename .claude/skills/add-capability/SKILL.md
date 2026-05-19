---
name: add-capability
description: Deprecated. Backend capabilities are now modules. Redirects to add-module.
argument-hint: "[capability name or description]"
---

The **add-capability** pattern (FastAPI router + Pydantic models under `platform/capabilities/`) is
no longer the canonical way to add backend logic to a Mozaiks app.

**Use the `add-module` skill instead.** Modules are the canonical deterministic backend unit in
Mozaiks. They follow the 4-layer contract (handler → service → repo → policy + schemas) and are
auto-discovered by the platform host at startup.

See: `.claude/skills/add-module/SKILL.md`
