# Mozaiks

<p align="center">
  <img src="assets/mozaik_logo.svg" width="160" alt="Mozaiks">
</p>

[![Version](https://img.shields.io/badge/version-0.1.0-blue)](https://github.com/BlocUnited-LLC/mozaiks/blob/main/CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/BlocUnited-LLC/mozaiks/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://www.python.org/)
[![AG2](https://img.shields.io/badge/AG2-Autogen-green)](https://github.com/ag2ai/ag2)

> **Note**: This is the unified Mozaiks stack. BlocUnited offers a managed platform with app generation tools at [mozaiks.ai](https://mozaiks.ai), but you're welcome to self-host and build everything yourself.

!!! tip "New to Development?"
    **Zero coding experience required!** Copy our [AI Setup Prompt](setup-prompt.md) into any AI coding agent (like [Claude Code](https://claude.ai/download), Cursor, or Copilot) and let AI guide you through the entire setup — from installing prerequisites to running your first app.

---

## 🎯 What is MozaiksAI?

**MozaiksAI Runtime** is a production-ready orchestration engine that transforms AG2 (Microsoft Autogen) into an app-grade platform with:

- ✅ **Event-Driven Architecture** → Every action flows through unified event pipeline
- ✅ **Real-Time WebSocket Transport** → Live streaming to React frontends
- ✅ **Persistent State Management** → Resume conversations exactly where they left off
- ✅ **Multi-Tenant Isolation** → app-scoped data and execution contexts
- ✅ **Dynamic UI Integration** → Agents can invoke React components during workflows
- ✅ **Declarative Workflows** → JSON manifests, no code changes needed
- ✅ **Comprehensive Observability** → Built-in metrics, logging, and token tracking

**MozaiksAI = AG2 + Production Infrastructure + Event-Driven Core**

---

## 🎨 See It In Action

### 🔀 Dual-Mode Interface

| Workflow Mode | Ask Mode |
|:---:|:---:|
| ![Workflow Mode](assets/ArtifactLayout.png) | ![Ask Mode](assets/AskMozaiks.png) |
| *Chat + Artifact split view* | *Full chat with history sidebar* |

### 💬 Embeddable Floating Widget

![Widget demo](assets/widgetAction.gif)

*Drop a floating assistant anywhere in your app — click the button to expand/collapse the chat interface*

---

## Next Steps

<div class="grid cards" markdown>

-   :material-robot: **AI-Assisted Setup**

    ---

    New to coding? Let AI set everything up for you.

    [:octicons-arrow-right-24: AI Setup Prompt](setup-prompt.md)

-   :fontawesome-solid-rocket: **Manual Setup**

    ---

    Clone, configure, and run the full stack yourself.

    [:octicons-arrow-right-24: Getting Started](getting-started.md)

-   :fontawesome-solid-sitemap: **Add a Workflow**

    ---

    Build your own AG2 workflow and wire it to the frontend.

    [:octicons-arrow-right-24: Adding a Workflow](guides/adding-a-workflow.md)

-   :fontawesome-solid-palette: **Brand Your App**

    ---

    Colors, fonts, logo, and nav from JSON files — no code changes.

    [:octicons-arrow-right-24: Customize Frontend](guides/customizing-frontend/01-overview.md)

</div>
