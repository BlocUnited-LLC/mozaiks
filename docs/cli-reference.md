# CLI Reference

The `mozaiks` CLI handles local workspace management — creating workspaces,
starting services, and checking status. Once things are running, use Studio for
everything else.

Run every command as `python -m mozaiks <command>`. The shorter `mozaiks`
command works too when your Python scripts directory is on `PATH`. Every
command accepts `--help`, and `python -m mozaiks --version` prints the
installed version.

## Quickstart

The single command most users need:

=== "Windows"

    ```powershell
    python -m mozaiks quickstart --dir .\my-workspace
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks quickstart --dir ./my-workspace
    ```

Scaffolds the workspace, starts the backend and frontend, and opens Studio at
`http://127.0.0.1:3000/apps`.

Run it with `--dir` from a Mozaiks source checkout. Without `--dir` there,
`quickstart` and `onboard` create no workspace: they rewrite the config of the
repository's own Studio bundle under `factory_app/app` instead.

## Quick Reference Table

| Command | What it does |
| --- | --- |
| `python -m mozaiks quickstart --dir <path>` | Create or reuse a workspace, start services, open Studio |
| `python -m mozaiks studio --dir <path>` | Print the workspace's app overview |
| `python -m mozaiks studio --dir <path> --open` | Start services for an existing workspace |
| `python -m mozaiks studio --dir <path> --json` | Print the app overview as JSON |
| `python -m mozaiks onboard --dir <path>` | Create a missing scaffold and set app name, provider, and model |
| `python -m mozaiks init <preset> --dir <path>` | Create a new app workspace scaffold |
| `python -m mozaiks serve <path>` | Start the platform host |
| `python -m mozaiks serve <path> --host studio` | Start the Studio host |
| `python -m mozaiks info` | Show the current preset and enabled features |
| `python -m mozaiks info --available` | List every preset and its features |
| `python -m mozaiks add <feature>` | Enable one feature in `app/app.json` |
| `python -m mozaiks add --preset <preset>` | Switch `app/app.json` to a preset |
| `python -m mozaiks gen {workflow,app} --prompt "..."` | Generate from a one-shot prompt |
| `python -m mozaiks migrations status` | Report generated-app migration health |
| `python -m mozaiks context index --app-id <id>` | Index a local workspace for App Intelligence |
| `python -m mozaiks sync-agent-guidance --dir <path>` | Check or repair coding-agent guidance files |

## Studio and Workspace Commands

### `quickstart`

Create a workspace with minimal defaults (or reuse an existing one), then start
the Studio backend and frontend and open Studio. This is the recommended first
command.

```text
mozaiks quickstart [-h] [--dir DIRECTORY] [--preset {engine,chat,integrated,full}]
                   [--name NAME] [--provider {anthropic,openai,local,other}]
                   [--model MODEL] [--backend-port BACKEND_PORT]
                   [--frontend-port FRONTEND_PORT] [--no-browser]
```

| Flag | Effect |
| --- | --- |
| `--dir DIRECTORY` | Workspace root to create or configure (default: current directory) |
| `--preset` | Scaffold preset used when the workspace has no valid app bundle (default: `chat`) |
| `--name NAME` | App name stored in the scaffold (default: workspace folder name) |
| `--provider` | Default AI provider recorded in `app/config/ai.json` (display only; see below) |
| `--model MODEL` | Default model name recorded in `app/config/ai.json` (display only; see below) |
| `--backend-port BACKEND_PORT` | Backend port (default: `8000`) |
| `--frontend-port FRONTEND_PORT` | Frontend port (default: `3000`) |
| `--no-browser` | Start the services without opening a browser |

The provider and model are shown by [`studio`](#studio), but builds do not
read them yet: the runtime builds OpenAI clients only, with `OPENAI_API_KEY`
and the model in `DEFAULT_LLM_MODEL` (default `gpt-5-nano`).

=== "Windows"

    ```powershell
    python -m mozaiks quickstart --dir .\my-workspace
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks quickstart --dir ./my-workspace
    ```

`quickstart` runs [`onboard`](#onboard) without prompts and then launches
Studio. It starts by printing the workspace path, followed by a warning if
`MONGO_URI` or a provider API key is not set:

```text
Quickstart workspace: /path/to/my-workspace
Bootstrapping workspace and opening Studio. Use Studio to create your first app.
```

The key warning also names `GEMINI_API_KEY` and `ANTHROPIC_API_KEY`, but only
`OPENAI_API_KEY` makes builds work today. The scaffold and config lines from
`onboard` follow, then:

```text
Setup complete.
Opening Studio...
Backend running (pid 12345); log: /path/to/my-workspace/logs/studio-backend.log
Frontend running (pid 12346); log: /path/to/my-workspace/logs/studio-frontend.log

Studio launched.
  Backend: http://127.0.0.1:8000
  Studio: http://127.0.0.1:3000/apps
```

Both servers listen on `127.0.0.1` only and write their output to the two log
files under the workspace's `logs/` folder. The first launch installs the
frontend's npm dependencies when they are missing, so it can take a few
minutes. To bind another interface, use [`studio --open --listen`](#studio).

### `studio`

Read an existing workspace and print its app overview, or start Studio for it
with `--open`.

```text
mozaiks studio [-h] [--dir DIRECTORY] [--json] [--open] [--backend-port BACKEND_PORT]
               [--frontend-port FRONTEND_PORT] [--no-browser] [--listen LISTEN]
```

| Flag | Effect |
| --- | --- |
| `--dir DIRECTORY` | Workspace root containing the app at `app/` (default: current directory) |
| `--open` | Start the backend and frontend and open Studio in the browser |
| `--json` | Print the app overview as JSON and exit |
| `--backend-port BACKEND_PORT` | Backend port used with `--open` (default: `8000`) |
| `--frontend-port FRONTEND_PORT` | Frontend port used with `--open` (default: `3000`) |
| `--no-browser` | With `--open`, start the services without opening a browser |
| `--listen LISTEN` | Interface the backend and frontend bind with `--open` (default: `127.0.0.1`, this machine only; use `0.0.0.0` for all interfaces). With authentication off, anyone who can reach that address can act as any user with any role, so the launcher prints a warning first. |

=== "Windows"

    ```powershell
    python -m mozaiks studio --dir .\my-workspace --open
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks studio --dir ./my-workspace --open
    ```

Without `--open` or `--json`, it prints a summary like this:

```text
App Overview

Workspace:         /path/to/my-workspace/app
Route:             /apps/my-app/overview
Local Only:        True
App:               My App
Provider / Model:  not configured / not configured
Refinement Engine: enabled
...
Runtime Readiness: entry_point_configured

Next Step:
  Confirm your default provider and model in app/config/ai.json before starting build work.

Use 'python -m mozaiks studio --json' for machine-readable output.
Launch Studio with: python -m mozaiks studio --dir <workspace> --open
```

The route uses the `appId` that `init` derives from the app name (`My App`
becomes `my-app`). If the folder has no valid scaffold, `studio` lists the
missing files, tells you to run `onboard` first, and exits with `1`.

### `onboard`

Set up a workspace step by step. If the folder has no valid scaffold, `onboard`
creates one; it then asks for the app name, AI provider, and default model,
writes them to the app config, and offers to open Studio.

```text
mozaiks onboard [-h] [--dir DIRECTORY] [--preset {engine,chat,integrated,full}]
                [--name NAME] [--provider {anthropic,openai,local,other}] [--model MODEL]
                [--non-interactive] [--open-studio] [--backend-port BACKEND_PORT]
                [--frontend-port FRONTEND_PORT] [--no-browser]
```

| Flag | Effect |
| --- | --- |
| `--dir DIRECTORY` | Workspace root containing the app at `app/` (default: current directory) |
| `--preset` | Preset used if a scaffold must be created (default: `chat`) |
| `--name NAME` | App name, instead of prompting |
| `--provider` | Default AI provider, instead of prompting |
| `--model MODEL` | Default model name, instead of prompting |
| `--non-interactive` | Use the current config and the flags given, without prompting |
| `--open-studio` | Launch Studio when onboarding finishes |
| `--backend-port BACKEND_PORT` | Backend port used when launching Studio (default: `8000`) |
| `--frontend-port FRONTEND_PORT` | Frontend port used when launching Studio (default: `3000`) |
| `--no-browser` | Launch Studio services without opening a browser |

=== "Windows"

    ```powershell
    python -m mozaiks onboard --dir .\my-workspace
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks onboard --dir ./my-workspace
    ```

On an empty folder, run with `--non-interactive --provider openai`, the output
ends:

```text
No valid Mozaiks scaffold found in /path/to/my-workspace
Bootstrapping a fresh 'chat' scaffold for my-workspace.

Created scaffold directories: app/config, app/security, app/services, app/modules, workflows, app/ui, app/brand
...
Updated app/app.json
Updated app/config/ai.json
Updated app/config/refinement_policy.yaml

Setup complete.
```

Always pass `--dir` when you run `onboard` from a Mozaiks source checkout.
Without it, `onboard` does not create a workspace: it finds the repository's
own Studio bundle under `factory_app/app` and rewrites its `config/ai.json`
and `config/refinement_policy.yaml`.

### `init`

Create a new app workspace scaffold: the `app/` bundle (config, brand, UI
manifest, data contract, and secrets policy), plus `requirements.txt`,
`.env.example`, a README, coding-agent guidance, and launch scripts. It does not
start anything. Apps themselves are created in Studio.

```text
mozaiks init [-h] [--name NAME] [--dir DIRECTORY] [--starter]
             [{engine,chat,integrated,full}]
```

| Argument | Effect |
| --- | --- |
| `preset` | Tier preset (default: `chat`). See [`info --available`](#info) for what each preset enables. |
| `--name NAME` | App name; when omitted, `init` prompts for it |
| `--dir DIRECTORY` | Target directory (default: derived from the app name) |
| `--starter` | Also add a starter workflow, `workflows/HelloWorkflow/` |

=== "Windows"

    ```powershell
    python -m mozaiks init chat --name "My App" --dir .\my-app
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks init chat --name "My App" --dir ./my-app
    ```

```text
Initializing Mozaiks project: My App
Preset: chat
Scaffold: blank
Target: my-app

Created scaffold directories: app/config, app/security, app/services, app/modules, workflows, app/ui, app/brand
Created app/app.json (preset=chat)
...
Created root consumer files: requirements.txt, .env.example, README.md, AGENTS.md, CLAUDE.md, scripts/, .claude/

Project initialized successfully.
```

The next steps that `init` prints use PowerShell syntax, and the generated
`scripts/` launchers are PowerShell scripts. On macOS and Linux, activate the
virtual environment with `source .venv/bin/activate`, copy the env file with
`cp .env.example .env`, and open Studio with
`python -m mozaiks studio --dir . --open`.

Until Mozaiks is published on PyPI, the printed step
`python -m pip install -r requirements.txt` fails, because the generated
`requirements.txt` pins `mozaiks==0.2.0`. Install Mozaiks into the workspace's
virtual environment from your checkout first
(`python -m pip install -e <path-to-mozaiks>`); pip then treats that pin as
already satisfied.

The `full` preset includes the admin portal, so `init` also asks for an admin
email (press Enter to skip). `init` refuses, with exit code `1`, to write into
a folder that already contains a scaffold, or into the framework repository
root.

## Runtime Command

### `serve`

Start a host directly, without the Studio frontend. Useful when you only need
the runtime.

```text
mozaiks serve [-h] [--host {runtime,platform,studio}] [--port PORT] [--listen LISTEN]
              [--reload]
              [workspace]
```

| Argument | Effect |
| --- | --- |
| `workspace` | Workspace root (default: current directory) |
| `--host` | Host layer to start (default: `platform`). `studio` needs `factory_app`, which a Mozaiks repo checkout provides. |
| `--port PORT` | Port to listen on (default: `8000`) |
| `--listen LISTEN` | Interface to bind (default: `127.0.0.1`, this machine only). Use `--listen 0.0.0.0` to listen on all interfaces, for example in a container. With authentication off, anyone who can reach that address can act as any user with any role, so `serve` prints a warning before it starts. |
| `--reload` | Enable uvicorn auto-reload (development only) |

```bash
python -m mozaiks serve ./my-app                            # platform host on :8000
python -m mozaiks serve ./my-app --host studio              # Studio host on :8000
python -m mozaiks serve ./my-app --host platform --reload   # with live reload
python -m mozaiks serve ./my-app --host platform --port 8001
```

On Windows, write the path as `.\my-app`.

`serve` looks for the app bundle at `<workspace>/app/app.json` or
`<workspace>/app.json` (or `factory_app/app` in the Mozaiks repo). It loads
`<workspace>/.env`, creating it from `.env.example` first when only the example
exists, then prints:

```text
App root : /path/to/my-app/app
Host     : platform  (127.0.0.1:8000)
```

The uvicorn startup log follows. Before printing these lines, `serve` checks
that MongoDB is reachable at `MONGO_URI`. If it is not, `serve` prints
`Error: MongoDB is not reachable at MONGO_URI (...)` with the command to rerun,
and exits with `1`.

## App Configuration Commands

Run `info` and `add` from the workspace root, the folder that contains
`app/app.json`. Anywhere else, both print `No app/app.json found.` and exit
with `1`.

### `info`

Show the app name, preset, and the features that preset enables, including any
per-feature overrides from `app/app.json`.

```text
mozaiks info [-h] [--available]
```

| Flag | Effect |
| --- | --- |
| `--available` | List every preset and the features it enables, instead of the current config |

```bash
python -m mozaiks info
```

```text
Current Configuration:

  App Name:      My App
  Preset:        chat
  Auth Required: False

Enabled Features:
    ✗ admin
    ✓ ai_runtime
    ✗ auth
    ✓ chat_ui
    ✗ event_bus
    ✗ modules

To enable more features, run:
  mozaiks add <feature>
  mozaiks add --preset <higher-tier>
```

A console that cannot print `✓` and `✗` shows `[x]` and `[ ]` instead.

`python -m mozaiks info --available` prints the presets:

```text
Available Tier Presets:

  engine       - AI workflow runtime only (headless API)
               Features: ai_runtime

  chat         - AI workflows + chat UI (chatbot builders)
               Features: ai_runtime, chat_ui

  integrated   - AI + chat + modules + event bus + auth (SaaS builders)
               Features: ai_runtime, modules, event_bus, auth, chat_ui

  full         - Everything including admin and subscriptions (full product builders)
               Features: ai_runtime, modules, event_bus, auth, admin, chat_ui
```

### `add`

Enable one feature, or switch the app to a different preset, by editing
`app/app.json`.

```text
python -m mozaiks add <feature>
python -m mozaiks add --preset <preset>
```

| Argument | Effect |
| --- | --- |
| `feature` | One of `modules`, `event_bus`, `auth`, `admin`, `chat_ui`. Sets `features.<feature>` to `true`. |
| `--preset PRESET` | One of `engine`, `chat`, `integrated`, `full`. Sets `preset` and removes per-feature overrides. |

Give exactly one of `feature` or `--preset`. Neither, both, or an unknown
value is a usage error and exits with `2`.

```bash
python -m mozaiks add auth
```

```text
Enabled feature: auth

Updated /path/to/my-app/app/app.json

Next Steps:
  - Add or review app/config/auth.yaml (schema_version: mozaiks.auth.v1)
  - Configure provider-neutral OIDC/JWT env handles in .env
  - Update authRequired in app/app.json

Restart the active host to apply changes, such as `python -m mozaiks serve . --host platform` or `python -m mozaiks studio --open`.
```

The next steps depend on what you enabled. Restart the running host to pick up
the change.

## Generation Command

### `gen`

Generate from a single prompt in the terminal. `gen` is a developer convenience;
reviewing, comparing, and promoting generated output happens in Studio.

```text
mozaiks gen [-h] [--prompt PROMPT] [--output OUTPUT]
            [--validation-strategy {e2b,docker,local,skip}] [--allow-interactive]
            [{workflow,app}]
```

| Argument | Effect |
| --- | --- |
| `mode` | `workflow` for agent workflows only, `app` for a full application |
| `--prompt`, `-p` | Description of what to build, at least 20 characters |
| `--output`, `-o` | Output directory (default: `./generated`) |
| `--validation-strategy` | App validation strategy (default: resolved from the current environment) |
| `--allow-interactive` | Start the run even when the workflow is conversational |

Without a mode or a prompt, `gen` asks for the mode, a multi-line description,
the output directory (default `./generated-<mode>`), and the validation
strategy.

`gen` refuses to start unless `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or
`AZURE_OPENAI_API_KEY` is set (`Error: No LLM API key found.`, exit code `1`).
The runtime builds OpenAI clients only, though, so a run that reaches a model
needs `OPENAI_API_KEY`. `MONGO_URI` is optional; without it, generation still
runs, but build records, artifacts, and usage events are not saved.

`gen` only runs one-shot workflows. Before any model call it reads the
workflow's own config, and a workflow that waits for a user reply is refused,
because the terminal cannot answer it. Both modes run the factory
AgentGenerator workflow, which opens by interviewing the user, so the run
stops there, points you to Studio, and exits with `1`:

```bash
python -m mozaiks gen workflow --prompt "A support assistant that drafts refund replies"
```

After the run banner, the output ends:

```text
Error: AgentGenerator is a conversational workflow: it asks clarifying questions and waits for your reply.
  `mozaiks gen` takes a single --prompt and cannot carry a reply, so this run would stall waiting for input after spending tokens and writing no files. Refusing before any model call.

Detected from the workflow's own config:
    - orchestrator.yaml declares human_in_the_loop: true
    - transition_graph.yaml hands control to the user (InterviewAgent -> user)

Run this generation in Studio instead:
    mozaiks studio --dir . --open
    Studio drives the conversation and supports replies.

To start the run anyway and drive it elsewhere, pass --allow-interactive (the run will still pause at the first question).
```

`--allow-interactive` starts the run anyway. It pauses at the first question,
spends tokens, and writes no files, so use it only for a run you will continue
somewhere else. A run that does complete prints `Generation complete!` and
lists the files it wrote; one that fails, pauses for input, or writes nothing
reports an error.

## Diagnostic Commands

### `migrations status`

Read generated-app data migration history and report failed, in-progress, and
unknown migrations. It only reads; it changes nothing. It reads MongoDB
through `MONGO_URI`.

```text
mozaiks migrations status [-h] [--app-id APP_ID] [--status STATUS] [--limit LIMIT]
                          [--database-name DATABASE_NAME] [--json]
```

| Flag | Effect |
| --- | --- |
| `--app-id APP_ID` | Report one app only |
| `--status STATUS` | Filter by status, such as `applied`, `in_progress`, or `failed` |
| `--limit LIMIT` | Maximum rows to return (default: `100`) |
| `--database-name DATABASE_NAME` | Override the migration history database name |
| `--json` | Print the full report as JSON |

```bash
python -m mozaiks migrations status --app-id my-app
```

```text
Migration health:
  total:       0
  applied:     0
  in_progress: 0
  failed:      0
  unknown:     0
  blockers:    no
  unknown_statuses: no

Rows: none
```

When there are rows, each one lists `app_id`, `migration_id`, `status`,
`applied_at`, `failed_at`, and `error_message`. The command exits with `1` when
the report has blockers or unknown statuses, and `2` when the report cannot be
loaded, so it can gate a script or CI step.

### `context index`

Scan a local workspace and register it for App Intelligence: it saves the
source context, builds an app context graph and an `AppIntelligenceSnapshot`,
and registers a new `AppContextVersion`. Results are saved as build records,
so it needs a reachable `MONGO_URI`. Studio stays the place to view the
results.

```text
mozaiks context index [-h] --app-id APP_ID [--workspace WORKSPACE]
                      [--artifact-key ARTIFACT_KEY] [--draft]
                      [--generated-artifacts-root GENERATED_ARTIFACTS_ROOT] [--json]
```

| Flag | Effect |
| --- | --- |
| `--app-id APP_ID` | Required. The app id to index the workspace under |
| `--workspace WORKSPACE` | Workspace root to scan (default: current directory) |
| `--artifact-key ARTIFACT_KEY` | Artifact key for the indexed app bundle (default: `app_intelligence_workspace`) |
| `--draft` | Register the `AppContextVersion` as a draft instead of making it current |
| `--generated-artifacts-root GENERATED_ARTIFACTS_ROOT` | Override where the indexed source bundle is written (default: `MOZAIKS_GENERATED_ARTIFACTS_PATH`, else `generated/`; a relative path resolves against the Mozaiks installation, not the workspace) |
| `--json` | Print the full result as JSON |

Run from a freshly initialized workspace:

```bash
python -m mozaiks context index --app-id my-app --workspace . --draft
```

```text
App Intelligence index registered.
  app_id: my-app
  app_bundle_artifact_version_id: av_...
  source_context_artifact_version_id: av_...
  app_intelligence_artifact_version_id: av_...
  app_context_version_id: ctx_my_app_av_...
  graph_artifact_version_id: av_...
  indexed_file_count: 30
  health_status: warning
  core_surface_file_count: 10
  warning: context_graph_sensitive_paths_skipped
  scan_warnings:
    - context_graph_sensitive_files_skipped:3
  artifact_path: /path/to/mozaiks/generated/app_intelligence/my-app/<timestamp>/artifact.zip
```

Health warnings, blockers, and scan warnings are listed above `artifact_path`
when there are any.

## Agent Guidance Command

### `sync-agent-guidance`

Check or repair the coding-agent guidance files in an app workspace:
`AGENTS.md`, `CLAUDE.md`, and the rules and skills under `.claude/`. `serve`,
`studio`, and `onboard` already refresh the Mozaiks-managed blocks in these
files, so you only need this command to inspect or repair them by hand.

```text
mozaiks sync-agent-guidance [-h] [--dir DIRECTORY] [--check] [--write-missing] [--update]
                            [--force]
```

| Flag | Effect |
| --- | --- |
| `--dir DIRECTORY` | Workspace root (default: current directory) |
| `--check` | Report status only. This is the default. |
| `--write-missing` | Create missing files; leave existing files unchanged |
| `--update` | Create missing files and update only the Mozaiks-managed blocks in existing files |
| `--force` | Overwrite every guidance file with the current template |

When more than one mode flag is given, the strongest wins: `--force`, then
`--update`, then `--write-missing`. Files without a Mozaiks-managed block are
reported as `unmanaged` and left alone unless you use `--force`.

```bash
python -m mozaiks sync-agent-guidance --dir .
```

```text
Agent guidance sync: /path/to/my-app
Mode: check
  [current] AGENTS.md - already matches template
  [current] CLAUDE.md - already matches template
  [current] .claude/rules/app-bundle.md - already matches template
  ...

Summary: current=14
```

In check mode, the command exits with `1` when any file is missing, outdated,
or unmanaged, and suggests the flag that applies changes.

## Troubleshooting

??? "Port already in use"

    === "Windows"

        ```powershell
        python -m mozaiks studio --dir .\my-workspace --open --backend-port 8001 --frontend-port 3001
        ```

    === "macOS / Linux"

        ```bash
        python -m mozaiks studio --dir ./my-workspace --open --backend-port 8001 --frontend-port 3001
        ```

??? "`mozaiks` command not found"
    Use `python -m mozaiks` instead. The `mozaiks` shortcut requires the Python
    scripts directory to be on PATH, which some systems do not configure automatically.

---

## Development Commands

For contributors working from a repo checkout:

| Command | What it does |
| --- | --- |
| `ruff check .` | Lint the codebase |
| `ruff check --fix .` | Lint and auto-fix |
| `mypy mozaiksai/ factory_app/ --ignore-missing-imports --disable-error-code=import-untyped` | Type-check the packages CI type-checks |
| `pytest` | Run all tests |
| `pytest tests/test_foo.py` | Run a single test file |
| `pytest tests/test_foo.py::test_bar` | Run a single test |

For full contributor setup see [Local Setup](local-setup.md).
