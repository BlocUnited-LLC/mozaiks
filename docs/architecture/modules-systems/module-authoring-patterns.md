# Module Authoring Patterns

This document captures common deterministic module implementation patterns.

These are authoring patterns, not runtime type declarations.
They do not change the canonical module contract described in
[Module System](module-system.md).

## Core Rule

Every module still uses the same contract:

- `module.yaml`
- optional `contracts/`
- `backend/handler.py`
- optional backend support files

Patterns change implementation emphasis, not file ownership.

## Common Patterns

### Registry and CRUD pattern

Use for:

- app registries
- listings
- profiles
- preference-backed records
- simple admin-managed datasets


The page surface still lives under `app/ui/pages/`.
The workflow layer remains separate.

### Transactional and ledger pattern

Use for:

- payments
- balances
- credits
- settlement projections
- auditable value movement

Keep the hosted proprietary engine separate from any app-facing facade.

### External adapter pattern

Use for:

- third-party webhook intake
- outbound service clients
- wrappers around existing external systems

The external system remains external.

## What Not To Do

- Do not model workflows as modules.
- Do not put persistent pages in module backend directories.
- Do not use legacy transport companion files as canonical module authoring outputs.
- Do not use legacy state-machine companion files as canonical module files.
- Use `schemas.py` as the canonical typed-shape file.

## Cross References

- [Module System](module-system.md)
- [Capability Pack Model](capability-pack-model.md)
- [Platform Authoring](../app/platform-authoring.md)
