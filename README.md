# Mozaiks

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/BlocUnited-LLC/mozaiks/main/docs/assets/logo-dark.png">
  <img src="https://raw.githubusercontent.com/BlocUnited-LLC/mozaiks/main/docs/assets/logo-light.png" alt="Mozaiks" width="260"/>
</picture>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/BlocUnited-LLC/mozaiks/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://www.python.org/)
[![AG2](https://img.shields.io/badge/AG2-1.0.1-green)](https://github.com/ag2ai/ag2)
[![Discord](https://img.shields.io/badge/Discord-join%20us-5865F2?logo=discord&logoColor=white)](https://discord.gg/Qnsywad9kp)

</div>

## What is Mozaiks?

Mozaiks is an open-source AI app factory for building, running, and iterating on
AI-native software products.

It brings together three things that usually live in separate tools:

- **Mozaiks Studio** for creating apps, continuing builds, and managing them.
- **AI workflow orchestration powered by AG2** for planning, tool use, human
  review, and generation.
- **Generated app files** with modules, pages, workflows, config, and brand
  assets that Mozaiks validates before making active.

The goal is not to generate a throwaway demo. Mozaiks stages production-shaped
artifacts, validates them against strict contracts, and keeps runtime concerns
separate from builder workflows.

## Quickstart

Five steps from a checkout to your first app in Studio.

### Prerequisites

- Python 3.11+
- Node.js 18+
- A reachable MongoDB database for workspace state

Docker Desktop is not required; use MongoDB Atlas, a local MongoDB install, or
Docker only if that is how you prefer to run MongoDB.

### 1. Install

Mozaiks is not published as a public PyPI package yet. Install it from a local
checkout in editable mode:

```powershell
python -m pip install -e ".[dev]"
```

### 2. Point Mozaiks at MongoDB

Studio stores workspace state in MongoDB, so configure it before opening Studio:

```powershell
# Local MongoDB
$env:MONGO_URI="mongodb://localhost:27017/mozaiks"

# Or MongoDB Atlas
$env:MONGO_URI="<your MongoDB connection string>"
```

### 3. Set an LLM key

Builds call an LLM, so set a key before running real builds. The default
provider is Google Gemini, which has a free tier (no credit card required):

```powershell
$env:GEMINI_API_KEY="your-key-here"
```

OpenAI and Anthropic work too — set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`
instead and select the provider in Studio or via `--provider`.

### 4. Create your workspace and open Studio

```powershell
python -m mozaiks quickstart --dir .\mozaiks-workspace
```

This creates `.\mozaiks-workspace` and starts the local Studio.

`.\mozaiks-workspace` is the local workspace folder Mozaiks uses for generated
output, config, and launch scripts. It is not the app itself. The app is
created later from inside Studio.

### 5. Build your first app

Open `http://localhost:3000/apps` and click `Create App`, then describe what you
want to build. The workflow walks you through the build steps and stages the
generated artifacts for review. In-progress builds stay in **Apps**, so you can
always pick up where you left off.

### Troubleshooting

**`mozaiks` is not recognized** — use `python -m mozaiks` instead. The `mozaiks`
shortcut requires your Python scripts directory to be on PATH, which some
systems do not configure automatically.

**Builds fail or hang** — make sure your LLM API key is set in the current shell
session. You can open Studio without a key, but builds will not run.

**Cannot reach MongoDB** — confirm `MONGO_URI` points at a database that is
actually running and reachable from this machine.

### Which Tool To Use

The **Studio** is the browser product for creating apps, continuing builds,
reviewing artifacts, and managing apps. The **CLI** is just how you set up the
local workspace, start processes, run diagnostics, and open Studio.

`studio` is also the host name used internally for that same browser product.
Most users can start from Studio and ignore the host details.

Studio itself is organized into two scopes:

- **Workspace dashboard** (`/apps`, `/usage`, `/integrations`) for your app
  inventory, workspace-wide token usage, and shared provider connections.
- **App dashboard** (`/apps/:appId/overview`) for a single app, covering
  Overview, Building, Branding, Launch, Growth, Users, Usage, Support, and
  Settings.

Main repo layout:

- `web_shell/` - local Vite shell host source
- `factory_app/app/` - first-party Studio app bundle and default brand assets
- `factory_app/workflows/` - shared builder workflow root

### Where To Go Next

| Guide | What it covers |
|---|---|
| [Use Studio](https://docs.mozaiks.ai/studio/) | The workspace and app-dashboard pages |
| [Add a Workflow](https://docs.mozaiks.ai/guides/adding-workflows/01-overview/) | Extend an app with a custom AI workflow |
| [Add a Module](https://docs.mozaiks.ai/guides/adding-modules/01-overview/) | Add a self-contained backend capability |
| [Add a Page](https://docs.mozaiks.ai/guides/adding-pages/01-overview/) | Add new pages and routes to your app workspace |
| [Config Files](https://docs.mozaiks.ai/guides/configs/) | Find the right file to edit |
| [Integrations](https://docs.mozaiks.ai/guides/integrations/01-overview/) | Connect shared services once and let apps declare what they need |
| [App Shell & Branding](https://docs.mozaiks.ai/guides/custom-brand-integration/01-overview/) | Themes, navigation, logos, and shell behavior |
| [Self-Hosting](https://docs.mozaiks.ai/guides/self-hosting/) | Run Mozaiks on your own server |

Want to contribute? See the [Contributing guide](https://docs.mozaiks.ai/contributing/).

## What The Framework Gives You

Generation is the visible part. What makes the generated output worth keeping is
the runtime underneath it, which is the same runtime whether an app was
generated or hand-written.

### Subscription to entitlement to feature gate

The pattern almost every SaaS rebuilds by hand. In Mozaiks it is a runtime
primitive: a module action names a capability, and the executor checks it before
dispatch.

```yaml
# modules/reports/module.yaml
actions:
  - id: export_report
    description: Export the current report as CSV
    handler_method: export_report
    entitlement_gate: reports.export    # checked before the handler runs
```

`app/config/subscriptions.yaml` declares which plans grant `reports.export`.
Apps with no subscriptions get `NoOpEntitlementAdapter` and are entirely
unaffected. Enforcement fails closed: an adapter that errors denies rather than
grants.

### Ports and adapters, not a framework you are stuck inside

Infrastructure sits behind protocols the runtime declares and never implements
for you:

| Port | Contract | Ships with |
|---|---|---|
| `EntitlementPort` | is this capability granted for this scope? | no-op + config-driven adapters |
| `ArtifactStore` | read/write named artifact blobs | local filesystem + S3 |
| `AppBackendPort` | runtime to backend request/emit/health | generic HTTP adapter |
| `SandboxPort` | isolated execution sessions | Docker adapter |
| `SslProviderPort` | certificate provisioning | protocol only |

Swap any of them for your own without forking the runtime.

### Contracts that are validated, not conventions that are hoped for

Every canonical YAML shape - modules, actions, events, reactions, pages,
workflows, data contracts - is backed by a strict typed model and validated
before it becomes active. A contract that cannot be generated repeatably and
validated deterministically is not treated as a contract.

### Execution that survives a crash

The workflow queue uses leases with fencing tokens, bounded retry, and
dead-lettering, so a worker dying mid-run does not strand or duplicate work.
Module dispatch requires an explicit authority object rather than trusting the
caller. Tenant isolation goes through one canonical scope filter instead of each
query hand-rolling its own.

### Apps you can leave with

Generated apps are provider-neutral and self-hostable: a Dockerfile, a compose
file, an env manifest, and staged data-contract migrations. Nothing requires
BlocUnited's hosted platform to run.

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
- [Releasing](https://github.com/BlocUnited-LLC/mozaiks/blob/main/docs/releasing.md) — Release hold and future publish workflow
- [Workflow Routing Transitions](https://github.com/BlocUnited-LLC/mozaiks/blob/main/docs/architecture/workflows/workflow-routing-transitions.md) — Flagship orchestration capability and runtime semantics
- [Workflow Authoring Contracts](https://github.com/BlocUnited-LLC/mozaiks/blob/main/docs/architecture/workflows/workflow-authoring-contracts.md) — Canonical strict YAML contract
- [Contributing](https://github.com/BlocUnited-LLC/mozaiks/blob/main/CONTRIBUTING.md) — Development workflow

Build the docs locally with `pip install -r requirements-docs.txt` and `./scripts/build-docs.ps1`.

---

## Contributing

Fork, branch, install development dependencies with `pip install -e ".[dev]"`,
make a focused change, run the relevant tests, and open a pull request.
Documentation, most tests, and many CLI changes don't need MongoDB, Node.js,
or an LLM API key. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full path.

Looking for a first issue? Start with
[good first issue](https://github.com/BlocUnited-LLC/mozaiks/labels/good%20first%20issue)
— comment on one to claim it before you start, so two people do not build the
same fix.

Using an AI coding agent? Welcome — read the
[AI Policy](.github/AI_POLICY.md) first. It is short, and it applies to
maintainers and their agents on the same terms.

Questions, or want to talk through an approach before writing code? Join us on
[Discord](https://discord.gg/Qnsywad9kp).

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

MIT. See [LICENSE](LICENSE).
