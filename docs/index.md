# Mozaiks

**Unified agentic stack** — contracts, core runtime, and orchestration.

![Mozaiks logo](assets/mozaik_logo.svg){ width="120" }

---

## What is Mozaiks?

Mozaiks is the unified stack for building AI-first applications:

| Layer | Contains |
|---|---|
| `contracts` | Envelopes, types, ports, schemas |
| `core` | API, persistence, auth, streaming, runtime services |
| `orchestration` | Runner, scheduler, adapters, execution state machine |

Consuming apps build product UX on top of this foundation.

## Import Direction

```
contracts ← core ← orchestration
```

> `core` must **never** import from `orchestration`.

## Execution Modes

- **Mode 1 — AI Workflow:** chat → agent → artifact
- **Mode 2 — Triggered Action:** button/API → function or mini-run
- **Mode 3 — Plain App:** pages/CRUD/settings without AI orchestration

## Quick Links

- [Agent Bootstrap](AGENT_BOOTSTRAP_PROMPT.md)
- [App Creation Guide](architecture/source-of-truth/APP_CREATION_GUIDE.md)
- [Workflow Architecture](architecture/source-of-truth/WORKFLOW_ARCHITECTURE.md)
- [Frontend Customization](guides/customizing-frontend/01-overview.md)
