# Mozaiks

> Runtime, orchestration, and contracts for AI-native applications.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## What is Mozaiks?

Mozaiks is a unified stack for building AI-native applications. It provides:

- **Contracts** (`mozaiks.contracts`) - Event envelopes, domain events, and port interfaces
- **Core Runtime** (`mozaiks.core`) - FastAPI server, persistence, WebSocket streaming, authentication
- **Orchestration** (`mozaiks.orchestration`) - AI workflow execution, AG2 adapters, deterministic scheduling

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
