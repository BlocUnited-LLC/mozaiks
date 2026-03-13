# Runtime Rules

Use these rules when touching runtime-oriented code such as:
- `mozaiksai/**`
- orchestration, adapters, transport, persistence, workers, or engine code

## Architecture Model

Map changes to the runtime primitives:
- `Application`
- `Run`
- `ExecutionWorker`
- `ExecutionEngine`
- `Event`

Ask which architecture layer the change belongs to:
- generator
- runtime
- execution engine

## Required Constraints

Keep the runtime:
- modular
- engine-agnostic
- event-driven
- multi-tenant safe
- declarative-first
- observable

Do not:
- hardcode workflows or product behavior into runtime internals
- add compatibility layers, aliases, or fallback adapters unless explicitly requested
- build new logic on obsolete workflow or groupchat abstractions
- weaken logging, metrics, execution events, or token accounting without an explicit reason

## Working Style

For each substantial runtime change:
1. identify the affected primitive
2. identify the minimal runtime boundary that should change
3. update only the necessary interfaces, adapters, and call sites

If a request would embed app-specific logic into the runtime, stop and clarify.