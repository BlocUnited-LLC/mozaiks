# Mozaiks

<div align="center" markdown>

<img src="assets/mozaik_logo.svg" alt="Mozaiks Logo" width="180"/>

[![Release](https://img.shields.io/github/v/release/BlocUnited-LLC/mozaiks)](https://github.com/BlocUnited-LLC/mozaiks/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/BlocUnited-LLC/mozaiks/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://www.python.org/)
[![AG2](https://img.shields.io/badge/AG2-Autogen-green)](https://github.com/ag2ai/ag2)

</div>

> **Note**: Mozaiks is open source. BlocUnited offers the managed product at
> [mozaiks.ai](https://mozaiks.ai), but you can self-host the framework and run
> the builder locally.

> **Start Here**: Install with `pip install mozaiks`, run
> `mozaiks quickstart --dir ./mozaiks-workspace`, then open the Console at
> `/apps`. See [Getting Started](getting-started.md).

> **Maintainers**: Use [Releasing](releasing.md) for the tag-driven package publish flow.

---

## What Is Mozaiks?

Mozaiks is an open-source AI app factory for building, running, and iterating on
AI-native software products.

It combines four pieces that usually live in separate tools:

- **Mozaiks Console** for creating apps, continuing builds, and managing app
  workspaces.
- **AG2 workflow orchestration** for multi-agent planning, tool use, human
  review, and mid-flight decomposition.
- **A generated app workspace contract** with modules, pages, workflows,
  config, and brand assets.
- **Production-readiness gates** for generated UI, workflow artifacts, package
  assembly, and runtime validation.

The goal is not to generate a throwaway demo. Mozaiks stages production-shaped
artifacts, validates them against strict contracts, and keeps runtime concerns
separate from builder workflows.

## Why It Exists

Most AI app builders optimize for one layer: a fast UI mockup, a chat agent, or
raw code edits. Mozaiks is designed around the full product loop:

- turn product intent into typed planning artifacts
- generate deterministic app files instead of ad hoc code dumps
- use shared UI primitives and brand tokens for consistent frontend output
- keep generated artifacts staged until they pass validation
- support refinement without rewriting the whole app from scratch

---

## 🎨 See It In Action

<div align="center" markdown>

### 💬 Embeddable Floating Widget

<video controls muted loop playsinline width="700">
  <source src="assets/widgetAction_compressed.mp4" type="video/mp4">
</video>

*Drop a floating assistant anywhere in your app - click the button to expand/collapse the chat interface*

---

### 🔀 Dual-Mode Interface

| Workflow Mode | Ask Mode |
|:---:|:---:|
| ![Workflow Mode](assets/ArtifactLayout.png) | ![Ask Mode](assets/AskMozaiks.png) |
| *Chat + Artifact split view* | *Full chat with history sidebar* |

</div>

---

## Start Here

Start with the Console. After install, `Create App` opens the builder workflow
sequence and walks you through app planning, generation, review, and refinement.

<div class="grid cards" markdown>

-   :material-rocket-launch: **Run Mozaiks Locally**

    ---

    Install the package, open the Console, and click `Create App`.

    [:octicons-arrow-right-24: Getting Started](getting-started.md)

-   :material-tune-variant: **Know What To Configure**

    ---

    Set one model key and MongoDB. Add vault or deployment settings only when needed.

    [:octicons-arrow-right-24: User Configuration](user-configuration.md)

-   :material-source-branch: **Develop Mozaiks Itself**

    ---

    Use a source checkout when you need editable framework, workflow, or Console code.

    [:octicons-arrow-right-24: Local Setup](local-setup.md)

-   :material-robot-outline: **Use an AI Coding Agent**

    ---

    Hand setup or feature work to Claude Code, Cursor, or Copilot with the repo-aware bootstrap prompt.

    [:octicons-arrow-right-24: Agent Bootstrap Prompt](agent-bootstrap-prompt.md)

-   :material-sitemap: **Build a Workflow**

    ---

    Add a deterministic AG2 workflow only when extending Mozaiks or an app workspace.

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
