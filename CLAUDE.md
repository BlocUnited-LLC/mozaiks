# Mozaiks Claude Context

This file is the top-level Claude project context.
Detailed scoped rules live under `.claude/rules/`.
Repeatable workflows live under `.claude/skills/`.

## Default Posture

Treat Mozaiks as an agentic runtime, not a product-specific agent app.

Favor:
- modular extensions over large core rewrites
- canonical replacements over compatibility shims
- declarative workflow changes over hardcoded runtime behavior
- concise, specific instructions over broad prose

## Runtime Priorities

For runtime changes, reason in terms of:
- `Application`
- `Run`
- `ExecutionWorker`
- `ExecutionEngine`
- `Event`

Protect:
- engine-agnostic boundaries
- event persistence and streaming
- async safety and event loop responsiveness
- tenant isolation across `app_id`, `user_id`, `chat_id`, and `run_id`

## Routing Guidance

When the request targets:
- `mozaiksai/`, `platform/`, `workers/`, `transport/`, or orchestration code: use the runtime rules first
- `chat-ui/` or `app/`: follow the frontend rules when those files are in scope
- `docs/`, `mkdocs.yml`, or Markdown files: follow the docs rules when those files are in scope

Do not put application logic into the runtime unless the user explicitly wants an architectural change there.

## Markdown Naming

When creating new Markdown files, prefer lowercase kebab-case names such as `conversation-modes.md`.

Use uppercase or special convention filenames only when required, for example `README.md`, `AGENTS.md`, `CLAUDE.md`, or `SKILL.md`.