# Contributing to Mozaiks

Mozaiks is open source under the MIT license. This section is for people working
on the framework itself — changing runtime code, adding factory workflows,
improving the Console, or maintaining the release pipeline.

If you are building an app with Mozaiks, you do not need this section.
Start with [Getting Started](../getting-started.md) instead.

## Where to start

- **[Local Dev Setup](../local-setup.md)** — source checkout, editable install, and how to run the builder stack from repo
- **[Architecture](../architecture/index.md)** — how the runtime, app bundle contract, module system, workflows, and frontend fit together
- **[Agent Bootstrap Prompt](../agent-bootstrap-prompt.md)** — hand a task to Claude Code, Cursor, or Copilot with the repo-aware bootstrap prompt
- **[Contributor Guidance Readiness](contributor-guidance-readiness.md)** — current skill coverage, routing map, deferrals, and guidance validation tests

## Release and Maintenance

- **[Releasing](../releasing.md)** — tag-driven PyPI publish flow
- **[Verified Setup Guide](../architecture/verified/setup-guide.md)** — maintainer-verified local environment
- **[Auth Setup](../architecture/verified/auth-setup.md)** — Keycloak and auth configuration
- **[Trigger Mechanisms](../architecture/verified/trigger-mechanisms.md)** — workflow trigger reference

## Repo Structure

The canonical repo layout is documented in
[ARCHITECTURE.md](https://github.com/BlocUnited-LLC/mozaiks/blob/main/ARCHITECTURE.md).

Key boundaries:

| Directory | Purpose |
|-----------|---------|
| `mozaiksai/` | AI runtime — workflow execution, transport, persistence |
| `factory_app/workflows/` | Factory layer — builder/generator workflows and agent configs |
| `factory_app/app/` | Studio first-party app bundle |
| `chat-ui/` | React component library |
| `web_shell/` | Local dev frontend shell |

## Design Principles

- **Structured-output-first** — every canonical YAML contract must be representable as a strict typed model
- **Declarative-first runtime** — no hardcoded workflow behavior in the runtime
- **No obsolete shims** — this repo is pre-production; replace outdated logic cleanly
- **Pre-production cleanup policy** — optimize for the cleanest canonical implementation, not stale-behavior preservation
