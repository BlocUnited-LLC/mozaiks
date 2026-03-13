# Engineering Agent Instructions

Use this file for repo-wide behavior that should apply across coding agents.
Keep Claude-specific workflow structure in `.claude/`.

## Scope

Mozaiks is an agentic runtime plus shared UI surfaces.

Default posture:
- treat runtime work as higher-risk than docs or app-surface work
- keep changes minimal and architecture-aware
- do not push product-specific behavior into runtime internals

## Runtime Model

When changing runtime code, map the work to these primitives:
- `Application`: persistent app definition and isolation boundary
- `Run`: execution instance and lifecycle
- `ExecutionWorker`: worker that performs run-scoped work
- `ExecutionEngine`: engine adapter providing computation
- `Event`: persisted and streamed execution signal

Runtime work must stay:
- engine-agnostic
- event-driven
- multi-tenant safe
- declarative-first
- observable

## Constraints

Do not:
- introduce compatibility layers, fallbacks, or legacy adapters unless explicitly requested
- build new logic on top of obsolete workflow or groupchat abstractions
- hardcode workflows or application behavior into runtime code
- allow cross-tenant leakage between `app_id`, `user_id`, `chat_id`, or `run_id`

Prefer canonical replacements and direct call-site updates.

## Working Rules

For each substantial change:
1. identify the runtime primitive or surface affected
2. identify whether the change belongs in runtime, generator, engine, docs, or frontend
3. make the smallest coherent change that preserves architecture boundaries

If a request would move application logic into the runtime, stop and clarify.

## Markdown Naming

When creating new Markdown files, prefer lowercase kebab-case names such as `conversation-modes.md`.

Keep uppercase or special convention filenames only when required by the ecosystem, for example `README.md`, `AGENTS.md`, `CLAUDE.md`, or `SKILL.md`.