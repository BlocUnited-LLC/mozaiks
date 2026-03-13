# API Reference Notes

This page is a lightweight reference note for the current Mozaiks backend surface.

For runtime behavior, prefer:

- [Workflow Architecture](../../architecture/foundations/workflow-architecture.md)
- [Event System Architecture](../../architecture/foundations/event-system-architecture.md)
- [Process and Event Map](../../architecture/foundations/process-and-event-map.md)

## Current Public Surfaces

The current web shell and platform primarily rely on:

- `GET /api/health`
- `GET /api/workflows`
- `GET /api/theme-config`
- `GET /api/navigation-config`
- module-related routes exposed by `mozaikscore/core/director.py`
- workflow and chat/session routes exposed by the shared app

## Where To Look In Code

- `shared_app.py`
- `mozaikscore/core/director.py`
- `mozaiksai/core/transport/`
- `mozaiksai/core/workflow/`

## Guidance

- Treat this file as a quick orientation note, not an exhaustive endpoint contract.
- When documenting or changing API behavior, update the architecture docs and the relevant route implementation together.
