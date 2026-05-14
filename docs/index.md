# Mozaiks

<div align="center" markdown>

<img src="assets/mozaik_logo.svg" alt="Mozaiks Logo" width="180"/>

[![Release](https://img.shields.io/github/v/release/BlocUnited-LLC/mozaiks)](https://github.com/BlocUnited-LLC/mozaiks/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/BlocUnited-LLC/mozaiks/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://www.python.org/)
[![AG2](https://img.shields.io/badge/AG2-Autogen-green)](https://github.com/ag2ai/ag2)

</div>

> **Mozaiks is open source.** BlocUnited offers the managed product at
> [mozaiks.ai](https://mozaiks.ai), but you can self-host the framework and run
> the builder locally.

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

## Where to Start

Install Mozaiks, open the Console, and click `Create App`. The builder walks you
through planning, generation, review, and refinement inside the chat experience.

<div class="grid cards" markdown>

-   :material-rocket-launch: **Install and Create Your First App**

    ---

    Install Mozaiks, open the Console, and build your first app in minutes.

    [:octicons-arrow-right-24: Get Started](getting-started.md)

-   :material-tune-variant: **Configure Your Environment**

    ---

    Set one model key and MongoDB. Everything else is optional until you need it.

    [:octicons-arrow-right-24: Configuration](user-configuration.md)

-   :material-sitemap: **Add a Workflow to Your App**

    ---

    Extend an app workspace with a custom AG2 workflow.

    [:octicons-arrow-right-24: Add Workflows](guides/adding-workflows/01-overview.md)

-   :material-view-dashboard-outline: **Customize Your App Shell**

    ---

    Change branding, layout chrome, auth, and shell behavior without touching core runtime code.

    [:octicons-arrow-right-24: App Shell & Branding](guides/custom-brand-integration/01-overview.md)

-   :material-source-branch: **Contributing**

    ---

    Working on the framework, factory workflows, or Console? Start here.

    [:octicons-arrow-right-24: Contributing](contributing/index.md)

</div>
