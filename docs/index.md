# Mozaiks

<div align="center">

<img src="assets/mozaik_logo.svg" alt="Mozaiks Logo" width="180"/>

**Open-source runtime, orchestration, and contracts for AI-native applications**

[![Version](https://img.shields.io/badge/version-0.1.0-blue)](https://github.com/BlocUnited-LLC/mozaiks/blob/main/CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/BlocUnited-LLC/mozaiks/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://www.python.org/)
[![AG2](https://img.shields.io/badge/AG2-Autogen-green)](https://github.com/ag2ai/ag2)

</div>

> **Note**: This is the unified Mozaiks stack. BlocUnited offers a managed platform with app generation tools at [mozaiks.ai](https://mozaiks.ai), but you're welcome to self-host and build everything yourself.

---

## What is Mozaiks?

Mozaiks is the unified stack for building AI-native applications. It provides:

- **Contracts** — Event envelopes, domain events, and port interfaces
- **Core Runtime** — FastAPI server, persistence, WebSocket streaming, authentication
- **Orchestration** — AI workflow execution, AG2 adapters, deterministic scheduling

---

## 🎨 See It In Action

<div align="center">

### 🔀 Dual-Mode Interface

| Workflow Mode | Ask Mode |
|:---:|:---:|
| ![Workflow Mode](assets/ArtifactLayout.png) | ![Ask Mode](assets/AskMozaiks.png) |
| *Chat + Artifact split view* | *Full chat with history sidebar* |

---

### 💬 Embeddable Floating Widget

<video width="100%" controls style="border-radius: 8px; margin: 1rem 0;">
  <source src="https://github.com/user-attachments/assets/32bc7ec8-f550-42f7-b287-3b015c5df235" type="video/mp4">
  <em>Drop a floating assistant anywhere in your app — click the button to expand/collapse the chat interface</em>
</video>

*Drop a floating assistant anywhere in your app — click the button to expand/collapse the chat interface*

</div>

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

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/BlocUnited-LLC/mozaiks.git
cd mozaiks
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env: set OPENAI_API_KEY and MONGO_URI

# 3. Start MongoDB
docker compose -f infra/compose/docker-compose.yml up mongo -d

# 4. Start backend
python run_server.py

# 5. Start frontend (separate terminal)
cd app && npm install && npm run dev
```

Visit [http://localhost:3000](http://localhost:3000) — the HelloWorld workflow is ready to run.

---

## Repository Layout

```text
mozaiks/
  mozaiksai/        ← runtime engine (transport, orchestration, persistence)
  chat-ui/          ← UI library (React, WebSocket adapter, ChatPage)
  app/              ← your app shell (brand it, extend it)
  workflows/        ← declarative workflow definitions
  shared_app.py     ← FastAPI server entry point
  run_server.py     ← start the server
  app.json          ← app name, API URL, default workflow
  .env.example      ← environment variable reference
  docs/             ← this documentation
```

---

## Architecture

Layer direction is strict:

```text
contracts ← core ← orchestration
```

`core` does not import from `orchestration`.

### Execution Modes

1. **Workflow Mode** — chat → agent → artifact (split-screen UI)
2. **Ask Mode** — conversational agent with session history sidebar
3. **Widget Mode** — floating assistant embedded in any page

---

## Next Steps

<div class="grid cards" markdown>

-   :fontawesome-solid-rocket: **Get Started**

    ---

    Clone, configure, and run the full stack in minutes.

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
