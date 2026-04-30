# API Reference Notes

This page is a lightweight reference note for the current Mozaiks backend surface.

For runtime behavior, prefer:

- [Workflow Architecture](../../architecture/foundations/workflow-architecture.md)
- [Event System](../../architecture/foundations/event-system.md)
- [Event Contracts](../../architecture/foundations/event-contracts.md)

## Current Public Surfaces

The current web shell and platform primarily rely on:

- `GET /api/health`
- `GET /api/workflows`
- `GET /api/theme-config`
- `GET /api/shell-config`
- workflow and chat/session routes exposed by the layered hosts

## Where To Look In Code

- `mozaiksai/hosts/runtime.py`
- `mozaiksai/hosts/platform.py`
- `mozaiksai/hosts/studio.py`
- `mozaiksai/hosts/mozaiks.py`
- `mozaiksai/core/transport/`
- `mozaiksai/core/workflow/`

## Guidance

- Treat this file as a quick orientation note, not an exhaustive endpoint contract.
- When documenting or changing API behavior, update the architecture docs and the relevant route implementation together.
