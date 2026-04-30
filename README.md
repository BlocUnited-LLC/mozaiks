# Mozaiks

<div align="center">

<img src="./docs/assets/mozaik_logo.svg" alt="Mozaiks Logo" width="180"/>

[![Version](https://img.shields.io/badge/version-0.1.0-blue)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://www.python.org/)
[![AG2](https://img.shields.io/badge/AG2-Autogen-green)](https://github.com/ag2ai/ag2)

</div>

## 🎯 What is MozaiksAI?
Production-ready declarative orchestration engine for AG2 (formerly Microsoft Autogen):

- ✅ **Event-Driven Architecture** — Runtime, app, workflow, UI, and hosted events stay separated by contract
- ✅ **Mid-Flight Journeys (MFJ)** — Run parallel workflows by fork/join with deterministic parent resume
- ✅ **Real-Time WebSocket Transport** — Live agent streaming to React frontends
- ✅ **Dynamic UI Integration** — Agents can invoke React components during workflows
- ✅ **Multi-Tenant Isolation** — App-scoped data and execution contexts
- ✅ **Declarative Workflows** — YAML manifests, no code changes needed
- ✅ **Comprehensive Observability** — Built-in metrics, logging, and token tracking
- ✅ **Persistent State Management** — Resume conversations exactly where they left off

**Soon:** `pip install mozaiksai`

Current repo layout:

- `web_shell/` - local Vite shell host source
- `mozaiks-platform/app/` - repo-local App Zero app workspace
- `factory_app/app/workflows/` - shared builder workflow root

---

## 🎨 See It In Action

<div align="center">

### 💬 Embeddable Floating Widget

![Widget Demo](./docs/assets/widgetAction.gif)

*Drop a floating assistant anywhere in your app — click the button to expand/collapse the chat interface*

---

### 🔀 Dual-Mode Interface

| Workflow Mode | Ask Mode |
|:---:|:---:|
| ![Workflow Mode](./docs/assets/ArtifactLayout.png) | ![Ask Mode](./docs/assets/AskMozaiks.png) |
| *Chat + Artifact split view* | *Full chat with history sidebar* |

</div>
---

## 📚 Documentation

- [Architecture Overview](ARCHITECTURE.md) — System design and component model
- [Getting Started](docs/getting-started.md) — Full setup guide
- [Mid-Flight Journeys](docs/reference/deep-dives/mid-flight-journeys.md) — Flagship orchestration capability and runtime semantics
- [Workflow Authoring Contracts](docs/architecture/foundations/workflow-authoring-contracts.md) — Canonical strict YAML contract
- [Contributing](CONTRIBUTING.md) — Development workflow

Build the docs locally with `pip install -r requirements-docs.txt` and `./scripts/build-docs.ps1`.

---

## Contributing

See `CONTRIBUTING.md`.

## License

MIT. See `LICENSE`.
