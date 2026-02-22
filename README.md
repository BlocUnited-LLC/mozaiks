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

<div align="center">

https://github.com/user-attachments/assets/32bc7ec8-f550-42f7-b287-3b015c5df235

*Drop a floating assistant anywhere in your app — click the button to expand/collapse the chat interface*

</div>

</div>

---

## Installation

```bash
pip install mozaiks
```

For development:
```bash
pip install mozaiks[dev]
```

## Quick Start

```python
from mozaiks import create_app

# Create the FastAPI application
app = create_app()
```

```python
from mozaiks.contracts import EventEnvelope, DomainEvent
from mozaiks.core.auth import get_user_context
from mozaiks.orchestration import KernelAIWorkflowRunner

# Use contracts for event handling
event = DomainEvent(
    event_type="task.completed",
    run_id="run-123",
    payload={"result": "success"}
)

# Create a workflow runner
runner = KernelAIWorkflowRunner()
```

## Package Structure

```
mozaiks/
├── contracts/          # Event envelopes, ports, schemas
│   ├── events.py       # EventEnvelope, DomainEvent
│   ├── ports/          # Abstract interfaces
│   └── ...
├── core/               # Runtime infrastructure
│   ├── api/            # FastAPI routes
│   ├── auth/           # JWT, WebSocket auth
│   ├── persistence/    # EventStore, checkpoints
│   ├── streaming/      # WebSocket transport
│   └── ...
└── orchestration/      # AI workflow execution
    ├── adapters/       # AG2, mock runners
    ├── scheduling/     # Deterministic scheduler
    ├── tools/          # Tool registry
    └── runner.py       # KernelAIWorkflowRunner
```

## Architecture

Mozaiks follows a layered architecture:

1. **Contracts Layer** - Pure data models and abstract interfaces (no I/O)
2. **Core Layer** - Runtime infrastructure (FastAPI, persistence, auth)
3. **Orchestration Layer** - AI workflow execution (builds on core)

Applications built on Mozaiks import from these layers:

```python
# Your app
from mozaiks.contracts import EventEnvelope
from mozaiks.core.persistence import EventStore
from mozaiks.orchestration import KernelAIWorkflowRunner
```

## Related Projects

- [mozaiks-platform](https://github.com/BlocUnited-LLC/mozaiks-platform) - First-class application built on Mozaiks

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions welcome! Please read our contributing guidelines before submitting PRs.

---

<div align="center">

**Built by [BlocUnited](https://blocunited.com)** · [mozaiks.ai](https://mozaiks.ai)

</div>
