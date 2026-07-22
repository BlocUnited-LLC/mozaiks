---
name: add-page
description: Add or update a Mozaiks app page, preferring declarative page schemas before custom React.
argument-hint: "[page goal]"
disable-model-invocation: true
---

Complete this page task: $ARGUMENTS

**Before starting:** `git fetch origin && gh pr list --state open && git log origin/main --oneline -3` — if another agent has an open PR touching the same files, wait for it to merge or branch off it instead of main.

1. Read `AGENTS.md` and `.claude/rules/frontend.md`.
2. Prefer a declarative page under `app/ui/pages/`.
3. Use custom React under `app/ui/pages/custom/` only when the declarative schema cannot express the surface.
4. Register routes in `app/ui/route_manifest.json`.
5. Register custom components in `app/ui/index.js`.
6. Put shell/navigation/mobile chrome changes in `app/config/shell.json`.
7. Check desktop and mobile layout behavior before finishing.
