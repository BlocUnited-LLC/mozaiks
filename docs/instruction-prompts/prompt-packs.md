# Prompt Packs For AI Coding Agents

This folder contains reusable prompt packs for Claude Code, Cursor, Copilot, and similar coding agents.

Use them when you want an AI agent to work on a Mozaiks task without improvising the platform rules from scratch.

## What Prompt Packs Are For

A prompt pack gives an AI coding agent:

- the system context it needs
- the files it should read first
- the constraints it must follow
- the expected outcome for the task

That is much more reliable than typing a one-line request and hoping the agent infers the platform correctly.

## When To Use Them

Use a prompt pack when you want help with:

- setting up the repo locally
- adding or editing workflows
- configuring branding and shell behavior
- wiring databases or auth
- working on telemetry

If an AI coding agent is involved, prefer a prompt pack first.

## How To Use A Prompt Pack

1. Pick the prompt pack that matches your task.
2. Tell your AI coding agent to read that file.
3. Add your app-specific requirements after the file path.

Example:

```text
I want to create a new Mozaiks workflow.

Please read:
docs/instruction-prompts/adding-workflows/01-overview.md

The workflow should be called CustomerSupport.
It should help users check order status and escalate billing issues.
```

## Prompt Pack Categories

### Getting Started

- environment setup
- full local setup from clone

### Adding Workflows

- planning workflows
- backend workflow files
- tools
- UI components
- testing

### App Planning

- decompose user intent into typed app concerns
- classify capabilities into modules, workflows, actions, and CRUD surfaces

### App Shell And Branding

- branding files
- shell configuration
- assets
- wiring
- auth

### Databases

- MongoDB
- Keycloak
- production setup

### Telemetry

- tracing
- cost tracking
- budget management

## Relationship To The Main Docs

The docs explain the platform.

The prompt packs help an AI coding agent act on those docs.

Use both together:

- read the guide
- hand the matching prompt pack to the AI agent

Recommended planning pack:

- [Decompose App Intent](app-planning/decompose-app-intent.md)

## Recommended Workflow

For the best developer experience:

1. Start in the main docs.
2. Confirm the target files and platform rules.
3. Use the matching prompt pack when an AI coding agent is going to do the work.

That keeps the human and the agent aligned on the same implementation path.
