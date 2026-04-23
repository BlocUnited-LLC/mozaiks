# Mozaiks

<div align="center">

<img src="./docs/assets/mozaik_logo.svg" alt="Mozaiks Logo" width="180"/>

[![Version](https://img.shields.io/badge/version-0.1.0-blue)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://www.python.org/)
[![AG2](https://img.shields.io/badge/AG2-Autogen-green)](https://github.com/ag2ai/ag2)

</div>

## 🎯 What is This?

This repo now uses four canonical host entrypoints:

- `runtime_app.py` - reusable runtime substrate
- `platform_app.py` - headless app host
- `studio_app.py` - local/private builder host
- `mozaiks_app.py` - hosted Mozaiks product host

`mozaiksai/` is the runtime layer inside that model. It is not the whole stack by itself.

### **mozaiksai Runtime**
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
- [Separation Plan](SEPARATION_PLAN.md) — How we're splitting runtime from template
- [Getting Started](docs/getting-started.md) — Full setup guide
- [Mid-Flight Journeys](docs/reference/deep-dives/mid-flight-journeys.md) — Flagship orchestration capability and runtime semantics
- [Workflow Authoring Contracts](docs/architecture/foundations/workflow-authoring-contracts.md) — Canonical strict YAML contract
- [Contributing](CONTRIBUTING.md) — Development workflow

---

## Declarative Contract Snapshot

Mozaiks workflows are authored as strict YAML bundles. The runtime validates
these contracts and rejects unknown fields.

```text
platform/workflows/{workflow_name}/
  orchestrator.yaml
  agents.yaml
  handoffs.yaml
  context_variables.yaml
  structured_outputs.yaml
  tools.yaml
  ui_config.yaml
  hooks.yaml
```

Minimal `context_variables.yaml` shape:

```yaml
definitions: {}
agents:
  GreeterAgent:
    variables: []
```

Use the canonical contract guide for full file schemas and required fields.

---

## Contributing

See `CONTRIBUTING.md`.

## License

MIT. See `LICENSE`.
