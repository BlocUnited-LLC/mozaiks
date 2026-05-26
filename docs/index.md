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

It brings together four things that usually live in separate tools:

- **Mozaiks Console** for creating apps, continuing builds, and managing them.
- **AI workflow orchestration powered by AG2** for planning, tool use, human
  review, and generation.
- **Generated app files** with modules, pages, workflows, config, and brand
  assets.
- **Validation checks** that review the generated output before it becomes the
  active app.

The goal is not to generate a throwaway demo. Mozaiks creates production-shaped
output, validates it, and keeps the app-building flow separate from the app
runtime.

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

Install Mozaiks, open the Console, and click `Create App`. Mozaiks then guides
you through planning, generation, review, and revision inside the chat.

<div class="grid cards" markdown>

-   :material-rocket-launch: **Install and Create Your First App**

    ---

    Install Mozaiks, open the Console, and build your first app in minutes.

    [:octicons-arrow-right-24: Get Started](getting-started.md)

-   :material-tune-variant: **Configuration Reference**

    ---

    Use this only when you need connector secrets, auth, or deployment settings.

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
