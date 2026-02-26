# Mozaiks

<div align="center">

<img src="./docs/assets/mozaik_logo.svg" alt="Mozaiks Logo" width="180"/>

**Open-source runtime, orchestration, and contracts for AI-native applications**

[![Version](https://img.shields.io/badge/version-0.1.0-blue)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://www.python.org/)
[![AG2](https://img.shields.io/badge/AG2-Autogen-green)](https://github.com/ag2ai/ag2)

</div>

> **Note**: This is the unified Mozaiks stack. BlocUnited offers a managed platform with app generation tools at [mozaiks.ai](https://mozaiks.ai), but you're welcome to self-host and build everything yourself.

---

## What is Mozaiks?

Mozaiks is the unified stack for building AI-native applications. It provides:

- **Contracts** (`mozaiks.contracts`) - Event envelopes, domain events, and port interfaces
- **Core Runtime** (`mozaiks.core`) - FastAPI server, persistence, WebSocket streaming, authentication
- **Orchestration** (`mozaiks.orchestration`) - AI workflow execution, AG2 adapters, deterministic scheduling

---

## 🎨 See It In Action

<div align="center">

### 🔀 Dual-Mode Interface

| Workflow Mode | Ask Mode |
|:---:|:---:|
| ![Workflow Mode](./docs/assets/ArtifactLayout.png) | ![Ask Mode](./docs/assets/AskMozaiks.png) |
| *Chat + Artifact split view* | *Full chat with history sidebar* |

---

### 💬 Embeddable Floating Widget

https://github.com/user-attachments/assets/32bc7ec8-f550-42f7-b287-3b015c5df235

*Drop a floating assistant anywhere in your app - click the button to expand/collapse the chat interface*

</div>

---

## 🎯 What is MozaiksAI?

**MozaiksAI Runtime** is a production-ready orchestration engine that transforms AG2 (Microsoft Autogen) into an app-grade platform with:

- ✅ **Event-Driven Architecture** -> Every action flows through unified event pipeline
- ✅ **Real-Time WebSocket Transport** -> Live streaming to React frontends
- ✅ **Persistent State Management** -> Resume conversations exactly where they left off
- ✅ **Multi-Tenant Isolation** -> app-scoped data and execution contexts
- ✅ **Dynamic UI Integration** -> Agents can invoke React components during workflows
- ✅ **Declarative Workflows** -> JSON manifests, no code changes needed
- ✅ **Comprehensive Observability** -> Built-in metrics, logging, and token tracking

**MozaiksAI = AG2 + Production Infrastructure + Event-Driven Core**

---

## Installation

```bash
pip install mozaiks
```

For local development:

```bash
pip install -e .[dev]
```

## Quick Start

```python
from mozaiks import create_app

app = create_app()
```

```python
from mozaiks.orchestration import KernelAIWorkflowRunner

runner = KernelAIWorkflowRunner(adapter="mock")
```

## Architecture

Layer direction is strict:

```text
contracts <- core <- orchestration
```

`core` does not import from `orchestration`.

### Execution Modes

Execution modes are app invocation flows, not runtime layers:

1. `Mode 1: AI Workflow` - chat to agent to artifact
2. `Mode 2: Triggered Action` - button/API trigger, optional AI
3. `Mode 3: Plain App` - standard pages/CRUD without AI

Artifacts bridge the modes.

## Repository Layout

```text
src/mozaiks/
  contracts/
  core/
  orchestration/
packages/frontend/chat-ui/
docs/architecture/source-of-truth/
tests/
```

## Source-of-Truth Docs

Start here for architecture:

- `docs/architecture/source-of-truth/README.md`
- `docs/architecture/source-of-truth/WORKFLOW_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/PROCESS_AND_EVENT_MAP.md`
- `docs/architecture/source-of-truth/EVENT_SYSTEM_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/EVENT_TAXONOMY.md`
- `docs/architecture/source-of-truth/GRAPH_INJECTION_CONTRACT.md`
- `docs/architecture/source-of-truth/LEARNING_LOOP_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/APP_CREATION_GUIDE.md`

## Practical Frontend Guide

- `docs/guides/CREATE_FRONTEND_WITH_MOZAIKS.md`

## Development Checks

```bash
pytest tests/ -v
mypy src/mozaiks/
ruff check src/
```

## Contributing

See `CONTRIBUTING.md`.

## License

MIT. See `LICENSE`.
