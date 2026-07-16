# Key Concepts

A quick mental model for Mozaiks. Read this once and the rest of the docs will
make sense.

## Workspace

A workspace is a local directory that holds your apps, Studio configuration,
and generated output. When you run `python -m mozaiks quickstart --dir my-workspace`,
Mozaiks creates the workspace and starts Studio inside it.

One workspace can contain multiple apps.

## App

An app is a named AI-native application inside a workspace. It has a lifecycle:

**Draft → Building → Review → Active**

An app bundle contains:

| Part | Purpose |
| --- | --- |
| **Modules** | Deterministic backend logic — CRUD, business rules, domain events |
| **Pages** | Frontend routes rendered by the platform shell |
| **Workflows** | AI-driven processes powered by AG2 |
| **Config** | Auth, subscriptions, AI startup, brand, and secrets |
| **Data contract** | The persistence schema for the app's modules |

## Studio

Studio is the browser management interface at `http://localhost:3000`. It is
where you create apps, continue builds, review generated output, and manage
access and usage.

Studio is **not** the app itself — it is the surface for building and operating apps.

## Platform Host

The platform host is the backend API server at `http://localhost:8000`. It runs
the app runtime: module actions, workflow execution, auth, and the frontend
shell. When you ship your app, the platform host is what your users talk to.

When building apps you mostly interact with Studio. The platform host is what
runs in production.

## Module

A module is a deterministic, self-contained backend capability: CRUD operations,
business rules, API endpoints, and domain events. Modules do not use AI — they
are your app's stable data and logic layer.

```
app/modules/orders/
├── module.yaml      ← identity, actions, permissions
├── backend/
│   ├── handler.py  ← thin action dispatch
│   ├── service.py  ← business logic and event emission
│   └── repo.py     ← database access
```

AI workflows call module actions to persist results. Modules and workflows
complement each other: modules own the data, workflows own the reasoning.

## Workflow

A workflow is an AI-driven process composed of agents, tools, and structured
outputs. Workflows handle planning, reasoning, generation, and multi-step
coordination. They are powered by AG2.

Two kinds:

| Kind | Where | Purpose |
| --- | --- | --- |
| **Factory workflows** | `factory_app/workflows/` | Shared builder workflows (AppGenerator, DesignDocs, ValueEngine) that run in Studio when you build an app |
| **App workflows** | `app/workflows/` or `workflows/` in the workspace | AI processes in your app — support chat, content generation, coding assistant |

## Page

A page is a frontend route in your app, declared in `app/ui/pages/` as YAML.
Pages are rendered by the platform shell and compose module data and workflow
interactions into a user-facing surface.

## The Build Sequence

A build is the full sequence of AI workflows that takes a plain-language
description and produces a complete app bundle. It runs inside Studio:

```
ValueEngine → DesignDocs → AppGenerator → AgentGenerator
```

| Workflow | What it does |
| --- | --- |
| **ValueEngine** | Captures the concept, target users, and value proposition |
| **DesignDocs** | Produces frontend, backend, and UX design intent |
| **AppGenerator** | Generates the app bundle: modules, pages, config, data contract |
| **AgentGenerator** | Generates AI workflow bundles when the app needs them |

In-progress builds are saved automatically. You can pick up where you left off
at any time from the **Apps** page.

## Generate and Promote

**Generate** is the AI process of producing app files. Generated output lands
in `generated/apps/{app_id}/{build_id}/app/` — it is staged, not live.

**Promote** is the explicit step that moves validated artifacts into the active
app bundle so the runtime loads them. Studio shows a **Promote** action when a
build is ready.

This two-step approach lets you review what the AI produced before anything goes
live in your app.

## CLI vs Studio

| Tool | Owns |
| --- | --- |
| **CLI** (`python -m mozaiks`) | Workspace creation, starting services, process management, status checks |
| **Studio** (browser) | Creating apps, continuing builds, reviewing artifacts, managing access and usage |

Typical flow: use the CLI once to start everything, then do everything else in
Studio. See the [CLI Reference](cli-reference.md) for all commands.
