# Mozaiks

<div align="center">

<img src="./docs/assets/mozaik_logo.svg" alt="Mozaiks Logo" width="180"/>

[![Version](https://img.shields.io/badge/version-0.1.0-blue)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://www.python.org/)
[![AG2](https://img.shields.io/badge/AG2-Autogen-green)](https://github.com/ag2ai/ag2)

</div>

> **Note**: This is the unified Mozaiks stack. BlocUnited offers a managed platform with app generation tools at [mozaiks.ai](https://mozaiks.ai), but you're welcome to self-host and build everything yourself.

> **Zero-Code Setup**: New to development? No problem! Copy the [AI Setup Prompt](https://docs.mozaiks.ai/setup-prompt/) into your AI coding agent (Claude Code, Cursor, Copilot, etc.) and let AI guide you through the entire setup.

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

## Installation

```bash
pip install mozaiks
```

For local development:

```bash
pip install -e .[dev]
```

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
