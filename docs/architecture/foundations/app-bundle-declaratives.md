# App Bundle Declaratives

**Last updated:** 2026-03-12  
**Status:** Current architecture reference  
**Audience:** Core maintainers, generator authors, and app-bundle designers

---

## Purpose

This document defines the declarative file families that Mozaiks Core should
consume from an application bundle.

The goal is to make app generation:

- structured
- opinionated
- scalable
- not dependent on freeform code generation for every app concern

This document exists because Mozaiks already has a strong workflow declarative
story, but its non-workflow app model has been underspecified.

If another doc is vague about how CRUD/basic app behavior should be represented,
this doc wins.

---

## Thesis

Mozaiks should not ask generators to "build an app" by directly improvising
React components and backend logic as the primary target.

Mozaiks should ask generators to emit a **compiled app bundle** with typed,
opinionated declaratives for:

- app identity and deployment
- AI/chat runtime behavior
- shell behavior
- module registration
- data entities
- CRUD views
- actions and integrations
- access policies
- workflows

Then the runtime and frontend shell consume those declaratives.

That is how Mozaiks scales beyond workflow demos.

---

## Before Files: The Missing Decomposition Step

The app bundle is the output target.

It is not the first thing the system should reason about.

Before generating any `platform/` files, Mozaiks should decompose user intent
into typed app concerns.

The intended sequence is:

```text
User intent
  -> Capability map
  -> EntitySpec / ViewSpec / ActionSpec / ModuleSpec / WorkflowSpec / PolicySpec
  -> Bundle plan
  -> platform/ files
```

This matters because a request like:

- `build me a marketplace`

does not immediately tell the system:

- which durable entities exist
- which surfaces should be modules
- which mutations should be actions
- which experiences require workflows

If Mozaiks skips this decomposition step, generation becomes shallow and
inconsistent.

See also:

- [App Creation Guide](app-creation-guide.md)
- [Builder Execution Model](builder-execution-model.md)

---

## The Bundle Families

An app bundle should be thought of as seven declarative families.

| Family | Purpose | Current path |
|---|---|---|
| App manifest | Deployment/platform identity | `platform/app.json` |
| AI manifest | Engine + chat/workflow startup behavior | `platform/config/ai.json` |
| Shell manifest | Landing spot + shell chrome + discover behavior | `platform/config/navigation_config.json` today |
| Theme manifest | Visual identity | `platform/config/theme_config.json` |
| Module registry | Durable app surfaces/pages | `platform/config/module_registry.json` |
| App model | Entities, views, actions, policies | Not first-class yet |
| Workflow model | AI workflows and orchestration graphs | `platform/workflows/**` |

Important distinction:

- The **workflow model** is only one part of the app bundle.
- The **app model** must become first-class too.

The practical classification rule is:

- data -> entities
- durable screens -> modules + views
- deterministic mutations -> actions
- role/plan access -> policies
- conversational or orchestrated intelligence -> workflows

---

## Canonical Layout

This is the target bundle layout Mozaiks should consume.

```text
platform/
├── app.json
│
├── config/
│   ├── ai.json
│   ├── navigation_config.json      # shell config today; should narrow to shell-only concerns
│   ├── theme_config.json
│   ├── module_registry.json
│   ├── notifications_config.json
│   ├── settings_config.json
│   └── subscription_config.json
│
├── entities/
│   └── *.json
│
├── views/
│   └── *.json
│
├── actions/
│   └── *.json
│
├── policies/
│   └── *.json
│
├── modules/
│   └── {module_name}/
│       ├── module.json
│       ├── handler.py
│       └── ui/
│           ├── index.js
│           └── *.jsx
│
└── workflows/
    ├── _pack/
    │   └── workflow_graph.json
    └── {workflow_name}/
        ├── orchestrator.yaml
        ├── agents.yaml
        ├── handoffs.yaml
        ├── context_variables.yaml
        ├── structured_outputs.yaml
        ├── tools.yaml
        ├── ui_config.yaml
        ├── hooks.yaml
        ├── tools/
        ├── ui/
        └── _pack/
            └── workflow_graph.json
```

---

## File Responsibilities

### 1. `platform/app.json`

This file is for deployment/platform identity only.

It should own:

- `appName`
- `appId`
- `apiUrl`
- `wsUrl`
- `platforms`
- `auth`
- `dev`

It should not own:

- workflow entry selection
- chat startup mode
- engine choice for workflow runtime
- shell routing behavior beyond deployment identity

Those belong in the AI or shell manifest.

### 2. `platform/config/ai.json`

This file is the AI runtime manifest.

It should own:

- `engine.framework`
- chat boot defaults like `chat.startup_mode`
- workflow startup defaults like `workflows.entry_point`
- future AI-runtime options that are app-level rather than workflow-level

Example:

```json
{
  "engine": {
    "framework": "ag2"
  },
  "chat": {
    "startup_mode": "workflow"
  },
  "workflows": {
    "entry_point": "GreenRoom"
  }
}
```

### 3. `platform/config/navigation_config.json`

Conceptually, this is the **shell manifest**.

Today it is still named `navigation_config.json`. That is acceptable for now,
but it should own only shell concerns:

- `landing_spot`
- discover/header/footer behavior
- optional static non-module pages

It should not be the canonical place where modules are declared.

Modules already have their own registry and should be derived into the shell.

The long-term shape should be:

- shell config describes shell chrome and optional static pages
- module registry describes module surfaces
- the shell combines them

### 4. `platform/config/theme_config.json`

This is the visual identity manifest.

It should own:

- identity
- assets
- colors
- fonts
- shell-level UI chrome styling

It should not own app logic.

### 5. `platform/config/module_registry.json`

This is the canonical module registry.

It should answer:

- what modules exist
- whether they are enabled
- what backend handler they use

It should not duplicate shell routing concerns more than necessary.

### 6. `platform/entities/*.json`

This is the missing declarative family Mozaiks needs for CRUD/basic app scale.

Each entity file should define:

- `name`
- `display_name`
- `fields`
- `relations`
- `validation`
- `indexes`
- `default_sort`

Example concerns:

- `Customer`
- `Order`
- `Product`
- `Project`
- `Ticket`

This is the canonical app-data layer.

### 7. `platform/views/*.json`

Views define how entities are surfaced.

They should represent:

- list views
- detail views
- create forms
- edit forms
- filters
- search
- sort
- tabs/sections

Example view kinds:

- `entity_list`
- `entity_detail`
- `entity_create`
- `entity_edit`
- `dashboard`
- `kanban`
- `table`

A generator should not default to inventing each page from scratch. It should
compile to view specs first.

### 8. `platform/actions/*.json`

Actions define executable operations.

This is how Mozaiks avoids forcing every operation into a full workflow.

Action kinds should include:

- `crud.create`
- `crud.update`
- `crud.delete`
- `service.call`
- `integration.sync`
- `ai.action`
- `workflow.start`

This is important: not all AI belongs in `workflows/`.

Some AI behavior is just a bounded operation:

- summarize a ticket
- score a lead
- rewrite product copy
- classify a message

Those should be representable as declarative `ai.action` actions.

### 9. `platform/policies/*.json`

Policies define access and behavior constraints.

They should cover:

- visibility
- editability
- role access
- subscription gates
- field-level restrictions
- action eligibility

This lets generators stay opinionated and safe without burying rules in prompts.

### 10. `platform/modules/{name}/module.json`

Modules are durable product surfaces/pages.

They should own:

- route identity
- page metadata
- component entry point
- relationship to views/actions

Modules should be the persistent UX layer beyond chat.

### 11. `platform/workflows/**`

Workflows remain the canonical AI orchestration layer.

They should own:

- conversational/multi-agent logic
- UI tools
- handoffs
- lifecycle hooks
- MFJ graphs
- workflow-local context

They should not be forced to represent all app logic.

---

## CRUD and Basic AI: The Scalable Model

The scalable app model is:

- **entities** define the data
- **views** define how data is presented
- **actions** define what can happen
- **policies** define what is allowed
- **modules** surface those views in durable app pages
- **workflows** handle rich AI orchestration and guided interaction

This is how Mozaiks supports:

- plain CRUD pages
- hybrid pages with AI assistance
- full workflow-driven experiences

without turning every use case into a groupchat.

---

## What Generators Should Emit

Generators should emit typed planning artifacts that compile into the bundle.

The generator should not directly target ad hoc files first.

### Minimum Structured Outputs

At minimum, the generator side should produce:

- `AppSpec`
- `ShellSpec`
- `EntitySpec[]`
- `ViewSpec[]`
- `ActionSpec[]`
- `PolicySpec[]`
- `ModuleSpec[]`
- `WorkflowSpec[]`

These are planning/compilation artifacts, not necessarily runtime files.

### Compiler Direction

| Structured output | Compiles to |
|---|---|
| `AppSpec` | `platform/app.json` + high-level bundle metadata |
| `ShellSpec` | `platform/config/navigation_config.json` today |
| `EntitySpec[]` | `platform/entities/*.json` |
| `ViewSpec[]` | `platform/views/*.json` |
| `ActionSpec[]` | `platform/actions/*.json` |
| `PolicySpec[]` | `platform/policies/*.json` |
| `ModuleSpec[]` | `platform/modules/*/module.json` + UI skeleton |
| `WorkflowSpec[]` | `platform/workflows/**` |

---

## Generator Responsibility Map

This is the opinionated split I would use for generator agents.

| Generator role | Output |
|---|---|
| Value / product-definition agent | `AppSpec` |
| Shell planner | `ShellSpec` |
| Data model planner | `EntitySpec[]` |
| CRUD/view planner | `ViewSpec[]` |
| Action / integration planner | `ActionSpec[]` |
| Policy planner | `PolicySpec[]` |
| Module planner | `ModuleSpec[]` |
| Workflow planner | `WorkflowSpec[]` |
| Bundle compiler | concrete files in `platform/` |

This keeps generation disciplined and reviewable.

---

## What Not To Do

Do not ask generators to:

- invent page/data structure from scratch every time
- treat workflows as the representation for all app behavior
- duplicate module declarations in multiple config files
- bury access rules in prompts instead of policies
- generate arbitrary React/Python first for simple CRUD when a view/action spec would do
- use prose as the orchestration contract when a typed file can exist

---

## The Core Boundary

Mozaiks Core should consume these declarative families.

Mozaiks Core should not hardcode builder-specific or app-specific meaning into
them.

Core should provide:

- loaders
- validation
- execution/runtime semantics
- eventing
- rendering hooks

The first-party product (`mozaiks.ai`) should provide:

- the generator
- the prompt strategy
- the compilation flow
- the first-party builder UX

That keeps core modular while still making app generation scalable.

---

## Transitional Notes

Current repo state:

- `navigation_config.json` still mixes shell and module-nav concerns
- `entities/`, `views/`, `actions/`, and `policies/` are not yet first-class
- workflows are much more mature than app-model declaratives

That is acceptable as a transition state.

The next architecture work should focus on making the missing app-model
declaratives real, not on adding more workflow complexity.

---

## Bottom Line

If Mozaiks wants to scale beyond workflow demos, it needs a compiled app bundle
contract with **both**:

- a strong workflow model
- a strong app/data model

The workflow model already exists.

The app/data model is the next thing to formalize.

