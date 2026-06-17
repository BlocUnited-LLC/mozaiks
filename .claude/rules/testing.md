---
paths:
  - "AGENTS.md"
  - "CLAUDE.md"
  - "docs/**/*.md"
  - ".claude/**/*.md"
  - "tests/**"
---

# Testing And Final Report Rules

Use these rules when reviewing, implementing, or finishing a change.

## Focused Validation

- Run the narrowest tests that exercise the touched layer first.
- Docs and guidance changes should add hygiene tests when possible.
- Do not treat `git diff` as a substitute when a focused test or lint command
  exists.
- Final reports must list the tests run, or state clearly when tests were not
  run.

## OSS Change Impact

- Layer changed
- Build workflow impact
- Runtime/platform impact
- App workspace impact
- Tests run
- Docs updated
- Compatibility risk

## Build Workflow Sequence Impact

- workflow_sequence affected
- workflows affected
- transitions affected
- entrypoints affected
- downstream artifacts affected
- tests run
- rollback risk

## Control-Plane / Refinement Impact

- classification affected
- artifact routing affected
- workflow sequence affected
- checkpoint/re-entry behavior
- tests run

## Module Contract Impact

- module files affected
- runtime loader impact
- generator/CLI impact
- compatibility policy
- tests run

## Managed Capability Boundary Check

- managed-capability or external-adapter surfaces affected
- app-owned facade boundary preserved
- no provider internals copied into OSS/runtime/app bundle output
- examples remain provider-neutral
- tests run
