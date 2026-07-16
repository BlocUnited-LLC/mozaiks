# Mozaiks

<div align="center" markdown>

<img src="assets/mozaik_logo.svg" alt="Mozaiks Logo" width="180"/>

[![Release](https://img.shields.io/github/v/release/BlocUnited-LLC/mozaiks)](https://github.com/BlocUnited-LLC/mozaiks/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/BlocUnited-LLC/mozaiks/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://www.python.org/)
[![AG2](https://img.shields.io/badge/AG2-1.0_beta-green)](https://github.com/ag2ai/ag2)

</div>

> **Mozaiks is open source.** BlocUnited offers the managed product at
> [mozaiks.ai](https://mozaiks.ai), but you can self-host the framework and run
> the builder locally.

---

## What is Mozaiks?

Mozaiks is an open-source AI app factory for building, running, and iterating on
AI-native software products.

It brings together three things that usually live in separate tools:

- **Mozaiks Studio** for creating apps, continuing builds, and managing them.
- **AI workflow orchestration powered by AG2** for planning, tool use, human
  review, and generation.
- **Generated app files** with modules, pages, workflows, config, and brand
  assets that Mozaiks validates before making active.

The goal is not to generate a throwaway demo. Mozaiks stages production-shaped
artifacts, validates them against strict contracts, and keeps runtime concerns
separate from builder workflows.

--- 

## See It In Action

<div align="center" markdown>

### Embeddable Floating Widget

<video controls muted loop playsinline width="700">
  <source src="assets/widgetAction_compressed.mp4" type="video/mp4">
</video>

*Drop a floating assistant anywhere in your app*

---

### Dual-Mode Interface

| Workflow Mode | Ask Mode |
|:---:|:---:|
| ![Workflow Mode](assets/ArtifactLayout.png) | ![Ask Mode](assets/AskMozaiks.png) |
| *Chat + Artifact split view* | *Full chat with history sidebar* |

</div>

---

## Get Started

<div class="grid cards" markdown>

-   :material-rocket-launch: **Build Your First App**

    ---

    Install Mozaiks, open Studio, and create your first app in minutes.

    [:octicons-arrow-right-24: Get Started](getting-started.md)

-   :material-lightbulb-outline: **Key Concepts**

    ---

    Workspaces, apps, modules, workflows, pages — the mental model in one page.

    [:octicons-arrow-right-24: Key Concepts](concepts.md)

-   :material-sitemap: **Add a Workflow**

    ---

    Extend an app with a custom AI workflow.

    [:octicons-arrow-right-24: Add Workflows](guides/adding-workflows/01-overview.md)

-   :material-view-dashboard: **Use Studio**

    ---

    Learn the workspace and app-dashboard surfaces.

    [:octicons-arrow-right-24: Studio](studio/index.md)

-   :material-puzzle-outline: **Add a Module**

    ---

    Add a self-contained backend capability to your app.

    [:octicons-arrow-right-24: Add a Module](guides/adding-modules/01-overview.md)

-   :material-file-document-outline: **Add a Page**

    ---

    Add new pages and routes to your app workspace.

    [:octicons-arrow-right-24: Add a Page](guides/adding-pages/01-overview.md)

-   :material-view-dashboard-outline: **Customize Branding**

    ---

    Change themes, navigation, and shell behavior.

    [:octicons-arrow-right-24: Branding](guides/custom-brand-integration/01-overview.md)

-   :material-server: **Self-Hosting**

    ---

    Run Mozaiks on your own server — Docker, Docker Compose, or Kubernetes.

    [:octicons-arrow-right-24: Self-Hosting](guides/self-hosting.md)

-   :material-robot-outline: **Use AI Coding Agents**

    ---

    Hand a task to Claude Code, Cursor, or Copilot with full Mozaiks repo context.

    [:octicons-arrow-right-24: Agent Bootstrap](agent-bootstrap-prompt.md)


</div>
