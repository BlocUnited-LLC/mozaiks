# Mozaiks

Mozaiks is the easiest framework for non-technical developers to build AI-native applications.

It gives you a modular runtime, a shared chat and artifact UI, declarative workflows, persistent modules, and a platform bundle under `platform/` that the runtime can consume directly.

If you are new here, think in three layers:

- `platform/` is the app bundle you customize
- `mozaiksai/` is the AI runtime and orchestration layer
- `chat-ui/` and `clients/mobile/` are the shared user interfaces

## Start Here

Choose the path that matches what you need to do.

<div class="grid cards" markdown>

-   :material-rocket-launch: **Run Mozaiks Locally**

    ---

    Clone the repo, configure the environment, and boot the stack.

    [:octicons-arrow-right-24: Getting Started](getting-started.md)

-   :material-robot-outline: **Use an AI Coding Agent**

    ---

    Hand setup or feature work to Claude Code, Cursor, or Copilot with the provided prompt packs.

    [:octicons-arrow-right-24: Prompt Packs](instruction-prompts/prompt-packs.md)

-   :material-sitemap: **Build a Workflow**

    ---

    Add agents, tools, handoffs, UI tools, and testing to a new workflow under `platform/workflows/`.

    [:octicons-arrow-right-24: Workflow Guide](guides/adding-workflows/01-overview.md)

-   :material-view-dashboard-outline: **Configure the App Shell**

    ---

    Customize branding, layout chrome, auth, and shell behavior without editing core runtime code.

    [:octicons-arrow-right-24: App Shell & Branding](guides/custom-brand-integration/01-overview.md)

-   :material-book-open-page-variant: **Understand the Architecture**

    ---

    Learn how the runtime, event bus, workflows, modules, and app bundle fit together.

    [:octicons-arrow-right-24: Architecture Overview](architecture/index.md)

-   :material-file-document-multiple-outline: **Open Advanced References**

    ---

    Read deep dives, runtime notes, and lower-level implementation guidance.

    [:octicons-arrow-right-24: Reference](reference/index.md)

</div>

## What You Can Build

Mozaiks is designed for more than chat bots. A single app bundle can mix:

- workflow-driven AI experiences
- persistent modules and pages
- inline UI tools inside chat
- artifact views beside chat
- standard app surfaces such as dashboards, forms, tables, and detail pages

The runtime already supports:

- AG2-powered workflow execution
- workflow-level fan-out and fan-in
- global workflow journeys
- WebSocket streaming and UI round-trips
- multi-tenant persistence
- event-driven orchestration

## How To Read The Docs

Use this order if you are learning the system:

1. [Getting Started](getting-started.md)
2. [Architecture Overview](architecture/index.md)
3. [Workflow Guide](guides/adding-workflows/01-overview.md)
4. [App Bundle Declaratives](architecture/foundations/app-bundle-declaratives.md)
5. [Prompt Packs](instruction-prompts/prompt-packs.md)

If you are already working with an AI coding agent, open:

- [Agent Bootstrap Prompt](agent-bootstrap-prompt.md)
- [Prompt Packs](instruction-prompts/prompt-packs.md)

## Current Platform Example

The live showcase in `platform/` is **Backstage**, a comedy-club operating system that demonstrates:

- workflow intake in `GreenRoom`
- workflow-level fan-out and fan-in in `WritersRoom`
- final presentation in `MainStage`
- inline UI tools, artifact UI tools, and persistent modules

This is the example to study instead of the older legacy demo flow.
