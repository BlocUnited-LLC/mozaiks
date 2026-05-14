# Surface Model

This document defines the surface types in Mozaiks and how they work together.

## Core Rule

Most product work in Mozaiks should fit one of three buckets:

- page
- workflow
- module

That is enough.

Do not start from backend ownership, deployment shape, or framework jargon. Start from what the thing is in the product.

## Quick Decision Rule

Use this order:

1. if it is a run, it is a `workflow`
2. if it is a screen, it is a `page`
3. if it is shared support logic, it is a `module`

## 1. Page

A page is a normal routeable app screen.

If the user can navigate to it as part of the product experience, it should usually be a page.

### Use a page when the thing is:

- discover
- archive
- lineup
- dashboard
- list
- detail
- form
- workspace

### What a page does:

- render UI
- call a module for data or actions
- launch a workflow through a page-owned action or entrypoint
- show the result of previous workflows

### What a page should not be:

- the workflow definition itself
- a giant declarative database schema

Pages are product surfaces, not infrastructure.

### Minimal page shape:

```yaml
name: archive
title: Archive
path: /archive
component: SchemaPage
showInNavigation: true
order: 20
uses:
  modules:
    - show_archive
  workflows: []
content:
  - type: Section
    title: Archive
    children: []
```

That is the right level of declaration for now.

## 2. Workflow

A workflow is agentic execution.

Use a workflow when the value comes from:

- reasoning
- orchestration
- multi-step generation
- HITL (human-in-the-loop)
- agent tools
- review loops

Question a workflow answers: **what run is happening**

Canonically, app-owned workflow definitions live under `app/workflows/*`.
The shared generation core lives under `factory_app/workflows/*`.

## 3. Module

A module is a support bundle behind pages.

Use a module when you need:

- shared page backing logic
- reusable handlers
- shared UI helpers
- workflow launch helpers

Question a module answers: **what shared support logic do multiple surfaces reuse**

Modules are real, but they should not be the first concept users think about.

## Page Composition

Pages should compose:

- local UI
- support modules
- optional workflow actions

They do not need to own the full backend model.

### Relationship to modules

Pages are the screen. Modules are the backing logic.

Good pattern:

- `archive` page renders the archive screen
- `show_archive` module provides the backing reads and actions

### Relationship to workflows

Pages can launch workflows.

Pages can also display workflow results.

But the workflow definition still lives under the app workspace's
`app/workflows/*`, or under the shared generation core for builder workflows.

## Current Examples

### `discover`

- `page`

### `archive`

- `page`

### `lineup`

- `page`

### `admin`

- first-class framework surface (not app-authored)

### `show_archive`

- `module`

### `lineup_board`

- `module`

### `GreenRoom`

- `workflow`

### `WritersRoom`

- `workflow`

### `MainStage`

- `workflow`

## Current Mozaiks Rule

For CRUD-like app building:

- start with a page
- add a module only if the page needs backing logic
- add a workflow only if reasoning or orchestration is actually needed

That keeps the system simple.

## What Not To Do

Do not:

- call every screen a module
- call every optional tool a separate service
- use workflow language for normal app pages
- force users to think in implementation internals before product surfaces

## Secondary Metadata

You may still have extra metadata such as:

- visibility
- order
- nav placement
- mount path

But those are secondary details. They are not the first classification question.

## Cross References

- [Frontend Architecture](../mozaiksai/index.md)
- [canonical-app-structure.md](canonical-app-structure.md)
- [../workflows/workflow-architecture.md](../workflows/workflow-architecture.md)
