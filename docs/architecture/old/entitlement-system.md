# Entitlement System

**Status:** Archived / superseded.

This page described a retired in-repo entitlement implementation and an older
backend/config layout. It is not
canonical for the current backend-agnostic Mozaiks runtime.

## Use These Instead

- `docs/architecture/foundations/account-admin-and-platform-services.md`
- `docs/architecture/foundations/canonical-app-structure.md`
- `docs/architecture/foundations/event-system.md`
- `docs/architecture/foundations/event-contracts.md`

## Current Rule

- Entitlement and subscription state are deterministic and backend-owned.
- Module-level gates and reactions are declared through
  `modules/{module}/subscriptions.yaml`.
- Optional deterministic implementation hooks live in
  `backend/subscriptions.py`.
- Notifications, admin surfaces, and workflow reactions should consume
  entitlement changes through the current event model rather than a retired
  in-repo entitlement subsystem.

If entitlement design needs to be expanded, update the current foundation docs
instead of restoring the historical model that used this file.
