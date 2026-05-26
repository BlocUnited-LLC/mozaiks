# Mozaiks

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/BlocUnited-LLC/mozaiks/main/docs/assets/logo-dark.png">
  <img src="https://raw.githubusercontent.com/BlocUnited-LLC/mozaiks/main/docs/assets/logo-light.png" alt="Mozaiks" width="260"/>
</picture>

[![Release](https://img.shields.io/github/v/release/BlocUnited-LLC/mozaiks)](https://github.com/BlocUnited-LLC/mozaiks/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/BlocUnited-LLC/mozaiks/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://www.python.org/)
[![AG2](https://img.shields.io/badge/AG2-Autogen-green)](https://github.com/ag2ai/ag2)

</div>

## What is Mozaiks?

Mozaiks is an open-source AI app factory for building, running, and iterating on
AI-native software products.

It combines three pieces that usually live in separate tools:

- **Mozaiks Console** for creating apps, reviewing builds, and managing app
  workspaces.
- **AG2 workflow orchestration** for multi-agent planning, tool use, human
  review, and mid-flight decomposition.
- **A generated app workspace contract** with modules, pages, workflows, config,
  and brand assets that can be validated before promotion.

The goal is not to generate a throwaway demo. Mozaiks stages production-shaped
artifacts, validates them against strict contracts, and keeps runtime concerns
separate from builder workflows.

## Quickstart

Install Mozaiks and open the Console:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install mozaiks
mozaiks quickstart --dir .\mozaiks-workspace
```

Then open `http://localhost:3000/apps` and click `Create App`.

`.\mozaiks-workspace` is the local workspace Mozaiks uses for Console state and
generated artifacts. The app itself is created from the Console.

### Console vs CLI

The **Console** is the browser product for creating apps, continuing builds,
reviewing artifacts, and managing app workspaces. The **CLI** is the local
developer entrypoint for creating a workspace, starting processes, running
diagnostics, and opening the Console.

`Studio` is the internal host name for the management server behind the
Console. You may see `studio` in architecture docs or host flags such as
`--host studio`, but users should start from the Console.

Want to contribute? See the [Contributing guide](https://docs.mozaiks.ai/contributing/).

Main repo layout:

- `web_shell/` - local Vite shell host source
- `factory_app/app/` - first-party Console app bundle and default brand assets
- `factory_app/workflows/` - shared builder workflow root

---

## 🎨 See It In Action

<div align="center">

### 💬 Embeddable Floating Widget

![Widget Demo](https://raw.githubusercontent.com/BlocUnited-LLC/mozaiks/main/docs/assets/widgetAction.gif)

*Drop a floating assistant anywhere in your app — click the button to expand/collapse the chat interface*

---

### 🔀 Dual-Mode Interface

| Workflow Mode | Ask Mode |
|:---:|:---:|
| ![Workflow Mode](https://raw.githubusercontent.com/BlocUnited-LLC/mozaiks/main/docs/assets/ArtifactLayout.png) | ![Ask Mode](https://raw.githubusercontent.com/BlocUnited-LLC/mozaiks/main/docs/assets/AskMozaiks.png) |
| *Chat + Artifact split view* | *Full chat with history sidebar* |

</div>
---

## 📚 Documentation

- [Architecture Overview](https://github.com/BlocUnited-LLC/mozaiks/blob/main/ARCHITECTURE.md) — System design and component model
- [Getting Started](https://github.com/BlocUnited-LLC/mozaiks/blob/main/docs/getting-started.md) — Full setup guide
- [Releasing](https://github.com/BlocUnited-LLC/mozaiks/blob/main/docs/releasing.md) — Tag-driven release and PyPI publish flow
- [Mid-Flight Journeys](https://github.com/BlocUnited-LLC/mozaiks/blob/main/docs/architecture/mozaiksai/mid-flight-journeys.md) — Flagship orchestration capability and runtime semantics
- [Workflow Authoring Contracts](https://github.com/BlocUnited-LLC/mozaiks/blob/main/docs/architecture/workflows/workflow-authoring-contracts.md) — Canonical strict YAML contract
- [Contributing](https://github.com/BlocUnited-LLC/mozaiks/blob/main/CONTRIBUTING.md) — Development workflow

Build the docs locally with `pip install -r requirements-docs.txt` and `./scripts/build-docs.ps1`.

---

## Contributing

See `CONTRIBUTING.md`.

## License

MIT. See `LICENSE`.
