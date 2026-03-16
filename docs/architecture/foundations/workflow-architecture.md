# Workflow Architecture

This document defines the role of workflows inside the broader Mozaiks
architecture.

Workflows are first-class, but they are not the entire app model.

## Core Rule

A workflow is an AI runtime unit for reasoning, orchestration, and HITL.

It is not:

- the canonical place to store business state
- the canonical source of product navigation
- the contract by which CRUD mutations trigger automation

Those concerns belong to the app substrate and automation boundary.

## Workflow Inputs

Workflows are declared under `platform/workflows/`.

Stable workflow files:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`
- `tools/*.py`
- `ui/*`
- `_pack/workflow_graph.json`

This file contract stays intact in this architecture rewrite.

## How Workflows Start

A workflow may be started in three ways.

### 1. Direct user entry

The user launches a workflow from a visible surface such as:

- chat
- a module action
- a shell entrypoint

### 2. Automation-triggered entry

A validated domain event matches an automation route and the AI runtime decides
to:

- run a workflow
- resume a workflow

This decision is owned by the AI side, not by the substrate emitter.

### 3. Internal orchestration entry

A workflow or workflow pack routes into another workflow through:

- pack graphs
- journeys
- explicit orchestration

## The Three Workflow Layers

### Workflow-local execution

Owned by the workflow's own files and the engine adapter.

Examples:

- agent roster
- prompts
- handoffs
- local tools
- UI pauses

### Workflow graph execution

Owned by compiled graph inputs such as:

- `platform/workflows/{name}/_pack/workflow_graph.json`
- `platform/workflows/_pack/workflow_graph.json`

These govern MFJ and journey-level sequencing.

### Automation routing into workflows

Owned by the automation boundary.

This is where a domain event becomes:

- `workflow.run`
- `workflow.resume`

The workflow runtime executes the resulting route. It does not own the policy
decision that selected the route.

## What Workflows Should Own

Workflows should own:

- reasoning
- contextual tool use
- structured human checkpoints
- orchestration
- artifact generation
- conversational guidance

## What Workflows Should Not Own By Default

Workflows should not be the default home for:

- basic CRUD
- list and detail screens
- deterministic form saves
- navigation declarations
- module registration
- direct domain event naming

If a capability can be expressed as an action and a view, start there first.

## Artifacts as the Bridge

Artifacts are the main bridge between workflow execution and durable app
surfaces.

Typical path:

1. workflow creates or updates an artifact
2. artifact is persisted
3. a module or view renders the artifact
4. a later action or workflow updates it

This lets workflows and the app substrate collaborate without collapsing into
one abstraction.

## Universal Orchestrator's Role

`UniversalOrchestrator` should stay coarse.

It should execute normalized workflow routes such as:

- run this workflow
- resume this workflow
- transfer between workflows

It should not become:

- the substrate event broker
- the CRUD layer
- the builder's entire control plane

An automation router or equivalent policy layer should sit in front of it for
domain-event-driven triggers.

## `_pack/workflow_graph.json`

The graph files may evolve in how they are produced, but their role stays the
same:

- they are compiled graph inputs
- they are not prose logic
- they are not the place where app domain policy lives

## Cross References

- [workflow-authoring-contracts.md](workflow-authoring-contracts.md)
- [declarative-ag2-mapping.md](declarative-ag2-mapping.md)
- [event-system-architecture.md](event-system-architecture.md)
