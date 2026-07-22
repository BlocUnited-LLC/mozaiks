---
name: docs-maintenance
description: Update app workspace docs safely when setup, modules, workflows, UI, or runtime behavior changes.
argument-hint: "[docs task]"
disable-model-invocation: true
---

Complete this docs task: $ARGUMENTS

**Before starting:** `git fetch origin && gh pr list --state open && git log origin/main --oneline -3` — if another agent has an open PR touching the same files, wait for it to merge or branch off it instead of main.

1. Keep README setup aligned with `requirements.txt`, `.env.example`, and `scripts/`.
2. Document new required environment variables in `.env.example`.
3. Prefer focused docs edits near the changed behavior.
4. Use lowercase kebab-case for new docs files unless a convention filename is required.
5. Remove stale instructions that assume a sibling Mozaiks framework checkout.
