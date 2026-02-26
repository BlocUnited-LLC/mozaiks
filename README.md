# Mozaiks

Open source contracts, runtime, and orchestration for AI-native applications.

Mozaiks provides a unified Python stack with three layers:

- `mozaiks.contracts`: event envelopes, schemas, and ports
- `mozaiks.core`: API/runtime infrastructure (auth, persistence, streaming, tools)
- `mozaiks.orchestration`: workflow execution and scheduling

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
