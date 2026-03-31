# Architecture

**Start here:** Read [/ARCHITECTURE.md](../../ARCHITECTURE.md) in the repo root. That's the authoritative reference.

## Quick Summary

Mozaiks has two backend services:

| Service | Purpose |
|---------|---------|
| `mozaiksai/` | AI workflow runtime (AG2 orchestration, WebSocket streaming) |
| `mozaikscore/` | App backend (REST API, user state, modules, subscriptions) |

Both read declarative configs from `platform/`.

## Detailed Topics

### Getting Started
- [Architecture Overview](foundations/architecture-overview.md) - The 4-line model and core concepts
- [Canonical App Structure](foundations/canonical-app-structure.md) - Platform directory structure

### For Building Apps
- [Surface Model](foundations/surface-model.md) - Pages, workflows, and modules
- [Platform Authoring](foundations/platform-authoring.md) - What to author under `platform/`
- [Event System](foundations/event-system.md) - Event model and taxonomy

### For Building Workflows
- [Workflow Architecture](foundations/workflow-architecture.md)
- [Workflow Authoring Contracts](foundations/workflow-authoring-contracts.md)

### For Subscription & Access Control
- [Entitlement System](foundations/entitlement-system.md) - Event-driven subscription gating

### For Frontend Work
- [Frontend Overview](frontend/index.md)

### Builder Product Note
- Builder workflow internals are maintained in a private `mozaiks-platform/` bundle and are intentionally excluded from OSS architecture docs.

## Service-Level Docs

Each service has a README with code-based documentation:
- [mozaiksai/README.md](../../mozaiksai/README.md)
- [mozaikscore/README.md](../../mozaikscore/README.md)
