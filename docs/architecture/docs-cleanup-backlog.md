# Docs Cleanup Backlog

**Status:** Working backlog  
**Last updated:** 2026-03-12

This file tracks documentation that is known to be stale, partially stale, or
written against retired repo shapes.

The goal is to keep architectural drift visible.

---

## Priority 1: High-Risk Stale Docs

These can actively mislead architecture or implementation work.

No open Priority 1 items are currently tracked in this backlog.

---

## Priority 2: Architecture Drift / Migration Context

These are less dangerous but still outdated enough to confuse future work.

No open Priority 2 items are currently tracked in this backlog.

---

## Priority 3: Instruction Prompt Drift

These may not break architecture directly, but they can cause generators or
contributors to author the wrong shapes.

No open Priority 3 items are currently tracked in this backlog.

---

## Cleanup Rules

When updating stale docs:

1. Prefer current repo paths over abstract or retired ones.
2. Prefer architecture foundations docs over migration notes.
3. Do not preserve retired showcase names as the canonical example.
4. Do not preserve `plugins` as the main platform vocabulary when the live unit
   is `modules`.
5. Reflect the current `platform/` bundle structure and `ai.json` ownership
   model.

---

## Recently Corrected

These were updated in the current cleanup pass:

- `docs/index.md`
- `docs/getting-started.md`
- `docs/instruction-prompts/prompt-packs.md`
- `docs/agent-bootstrap-prompt.md`
- `docs/architecture/index.md`
- `docs/reference/index.md`
- `docs/reference/deep-dives/index.md`
- `docs/architecture/foundations/canonical-app-structure.md`
- `docs/architecture/foundations/workflow-architecture.md`
- `docs/architecture/foundations/event-system-architecture.md`
- `docs/architecture/foundations/process-and-event-map.md`
- `docs/architecture/keycloak-auth.md`
- `docs/architecture/events/event-system-inventory.md`
- `docs/architecture/events/declarative-runtime-system.md`
- `docs/architecture/events/overview.md`
- `docs/guides/custom-brand-integration/*`
- `docs/instruction-prompts/custom-brand-integration/*`
- `docs/reference/deep-dives/mozaiks-platform-dual-substrate.md`
- `docs/instruction-prompts/workflows/create-new-workflow.md`
- `docs/instruction-prompts/adding-workflows/*`
- `docs/guides/mobile/*`
- `docs/reference/deep-dives/mid-flight-journeys.md`
- `docs/architecture/pipeline-refactor-plan.md`

---

## Next Recommended Pass

No open backlog items are currently tracked in this file.

