# Canonical App Structure

**Last updated:** 2026-02-26  
**Audience:** OSS developers building on mozaiks  
**Prerequisites:** [APP_CREATION_GUIDE.md](APP_CREATION_GUIDE.md), [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md)

---

## Overview

This document defines the canonical structure for consuming applications that run on mozaiks.

Goals:

- Keep app-owned product code separate from mozaiks runtime code.
- Keep workflow runtime inputs (`workflows/*`) explicit and portable.
- Support hybrid apps (Mode 1 + Mode 2 + Mode 3).

---

## Canonical Structure

```text
my_app/
├── app.yaml
├── requirements.txt
├── package.json
│
├── backend/
│   ├── api/
│   │   └── {resource}/
│   │       ├── routes.py
│   │       └── handlers.py
│   ├── services/
│   │   └── {service_name}.py
│   ├── models/
│   │   └── {model}.py
│   ├── connectors/
│   │   └── {service}.py
│   └── main.py
│
├── frontend/
│   ├── pages/
│   │   └── {route}/Page.tsx
│   ├── components/
│   │   └── {Component}.tsx
│   └── index.tsx
│
├── workflows/
│   └── {workflow_name}/
│       ├── backend/
│       │   ├── orchestrator.yaml
│       │   ├── agents.yaml
│       │   ├── tools.yaml
│       │   ├── events.yaml
│       │   ├── context_variables.yaml
│       │   ├── graph_injection.yaml
│       │   ├── notifications.yaml or notifications/*.yaml
│       │   ├── subscription.yaml  or subscription/*.yaml
│       │   ├── settings.yaml      or settings/*.yaml
│       │   └── stubs/
│       │       ├── tools/*.py
│       │       └── hooks/*.py
│       └── frontend/
│           ├── theme_config.json
│           ├── assets/*
│           └── components/*.{js,jsx,ts,tsx}
│
├── schemas/
│   ├── {model}.py
│   └── {model}.ts
│
├── config/
│   ├── settings.yaml
│   ├── theme.yaml
│   └── navigation.yaml
│
├── migrations/
│   └── {version}_{name}.sql
│
└── tests/
    ├── backend/
    ├── frontend/
    └── workflows/
```

---

## Directory Reference

### `backend/`

App-owned APIs and business logic.

| Path | Purpose |
|---|---|
| `api/` | REST/GraphQL endpoints |
| `services/` | Domain logic |
| `models/` | App DB models |
| `connectors/` | External service clients |
| `main.py` | App backend entrypoint |

### `frontend/`

App-owned product UI.

| Path | Purpose |
|---|---|
| `pages/` | Route-level screens |
| `components/` | Reusable app UI |
| `index.tsx` | Frontend bootstrap |

### `workflows/`

Workflow runtime inputs consumed by mozaiks.

| Path | Purpose |
|---|---|
| `workflows/{name}/backend/` | YAML runtime inputs + stubs |
| `workflows/{name}/frontend/components/` | Workflow artifact/fullscreen components |
| `workflows/{name}/frontend/theme_config.json` | Workflow UI identity |

Workflow discovery anchor:

- `workflows/*/backend/orchestrator.yaml`

### `schemas/`

Shared types between app backend, app frontend, and workflow tools.

### `config/`

App-level (not workflow-level) configuration.

### `migrations/`

App database migrations only.

---

## Required Workflow Files

Minimum per workflow:

- `backend/orchestrator.yaml`
- `backend/agents.yaml`
- `backend/tools.yaml`

Required when capability is used:

- `backend/stubs/tools/*.py` for declared tools
- `backend/events.yaml` for workflow custom events
- `backend/context_variables.yaml` for app-data/context bindings
- `backend/graph_injection.yaml` for FalkorDB injection/mutation
- `frontend/components/*` for custom artifact/fullscreen rendering

Optional declarative business configs:

- `backend/notifications.yaml` or `backend/notifications/*.yaml`
- `backend/subscription.yaml` or `backend/subscription/*.yaml`
- `backend/settings.yaml` or `backend/settings/*.yaml`

---

## Data and Control Boundaries

- Artifacts produced by workflows are persisted via mozaiks persistence/event store.
- App-owned CRUD/domain data lives in the app database.
- Mode 3 pages read artifacts via API queries.
- Mode 2 actions call direct functions or mini-runs.

---

## What Not to Put in the App Repo

Do not implement runtime internals here:

- event bus internals
- orchestration runtime engine internals
- core auth middleware internals
- core streaming transport internals

Those belong in `mozaiks` (`src/mozaiks/core` and `src/mozaiks/orchestration`).

---

## Quick Reference

| Question | Canonical answer |
|---|---|
| Where do workflow YAML files live? | `workflows/{name}/backend/` |
| Where do tool stubs live? | `workflows/{name}/backend/stubs/tools/` |
| Where do workflow UI components live? | `workflows/{name}/frontend/components/` |
| Where do app endpoints live? | `backend/api/` |
| Where do app pages live? | `frontend/pages/` |
| Where do shared schemas live? | `schemas/` |

---

## Validation Checklist

- [ ] Every workflow has `workflows/*/backend/orchestrator.yaml`.
- [ ] Every declared tool has a matching stub implementation.
- [ ] Workflow UI components are under `workflows/*/frontend/components/`.
- [ ] App-level CRUD/domain code is under `backend/` and `frontend/`.
- [ ] No core/orchestration runtime internals are reimplemented in the app repo.
- [ ] `graph_injection.yaml` and `_pack/workflow_graph.json` are treated as separate concerns.
