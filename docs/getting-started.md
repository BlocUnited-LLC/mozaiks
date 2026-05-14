# Getting Started

This is the shortest path to running Mozaiks and opening the Console.

## Install

You need:

- Python 3.11+
- Node.js 18+
- MongoDB locally or in Atlas
- one LLM provider key, usually `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`

Create a virtual environment and install Mozaiks:

=== "PowerShell"

    ```powershell
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install mozaiks
    ```

=== "macOS / Linux"

    ```bash
    python -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install mozaiks
    ```

## Configure

Set one model key and MongoDB:

=== "PowerShell"

    ```powershell
    $env:OPENAI_API_KEY="sk-..."
    $env:MONGO_URI="mongodb://localhost:27017/mozaiks"
    ```

=== "macOS / Linux"

    ```bash
    export OPENAI_API_KEY="sk-..."
    export MONGO_URI="mongodb://localhost:27017/mozaiks"
    ```

Use `ANTHROPIC_API_KEY` instead of `OPENAI_API_KEY` if that is your provider.

## Open The Console

Create a workspace and start Mozaiks:

=== "PowerShell"

    ```powershell
    mozaiks quickstart --dir .\my-first-mozaiks-app
    ```

=== "macOS / Linux"

    ```bash
    mozaiks quickstart --dir ./my-first-mozaiks-app
    ```

Open:

```text
http://localhost:3000/apps
```

Click `Create App` to start the build workflow.

## Use A Repo Checkout

Use a source checkout only when you are developing Mozaiks itself:

=== "PowerShell"

    ```powershell
    git clone https://github.com/BlocUnited-LLC/mozaiks.git
    cd mozaiks
    .\scripts\bootstrap-builder.ps1 -Workspace .\my-first-mozaiks-app
    ```

=== "macOS / Linux"

    ```bash
    git clone https://github.com/BlocUnited-LLC/mozaiks.git
    cd mozaiks
    ./scripts/bootstrap-builder.sh --workspace ./my-first-mozaiks-app
    ```

Use [Local Setup](local-setup.md) for debugging, editable installs, and
framework development.

## What You Get

Mozaiks starts the first-party Console and builder workflow root:

- `Apps` for app records, drafts, and build continuation
- `Create App` for the build workflow sequence
- generated app artifacts staged under a workspace before promotion
- shared UI primitives, theme tokens, module contracts, and workflow contracts
