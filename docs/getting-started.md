# Getting Started

## Prerequisites

- Python 3.11+
- Node.js 18+
- Docker Desktop — [download here](https://www.docker.com/products/docker-desktop/) (used to run MongoDB)

Mozaiks installs Tree-sitter parser packages with the core framework checkout
so Studio can build source-backed App Intelligence for generated and existing
apps. Mozaiks is not published on PyPI yet, so install it from this repo
instead of `pip install mozaiks`.
No separate parser setup is required.

## 1. Install

=== "Windows"

    ```powershell
    python -m pip install -e ".[dev]"
    ```

=== "macOS / Linux"

    ```bash
    python -m pip install -e ".[dev]"
    ```

## 2. Start MongoDB

Mozaiks requires a database to store your apps, build history, and workspace
state. The easiest way to start one is via Docker:

=== "Windows"

    ```powershell
    docker run -d --name mozaiks-mongo -p 27017:27017 mongo:7
    ```

=== "macOS / Linux"

    ```bash
    docker run -d --name mozaiks-mongo -p 27017:27017 mongo:7
    ```

!!! note "Don't want Docker?"
    Use [MongoDB Atlas](https://www.mongodb.com/atlas) (free tier, cloud-hosted,
    nothing to install). Set `MONGO_URI` to your Atlas connection string in step 3.

## 3. Set environment variables

=== "Windows"

    ```powershell
    $env:MONGO_URI="mongodb://localhost:27017/mozaiks"
    $env:GEMINI_API_KEY="your-key-here"
    ```

=== "macOS / Linux"

    ```bash
    export MONGO_URI="mongodb://localhost:27017/mozaiks"
    export GEMINI_API_KEY="your-key-here"
    ```

!!! tip "Free LLM key — Google Gemini"
    The default provider is **Google Gemini**, which has a free tier (no credit card required).
    Get your key at [aistudio.google.com](https://aistudio.google.com) → **Get API key**.

!!! note "Other providers"
    Using Atlas? Replace `MONGO_URI` with your Atlas connection string.

    Prefer OpenAI? Set `OPENAI_API_KEY=sk-...` and `LLM_PRIMARY_API_TYPE=openai` instead.

    Using Anthropic? Set `ANTHROPIC_API_KEY=sk-ant-...` and `LLM_PRIMARY_API_TYPE=anthropic`.

## 4. Create your workspace and open Studio

Replace `my-workspace` with whatever you want to name your app folder:

=== "Windows"

    ```powershell
    python -m mozaiks quickstart --dir .\my-workspace
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks quickstart --dir ./my-workspace
    ```

This scaffolds the workspace, starts the backend and frontend, and opens
Studio in your browser at `http://localhost:3000`.

## 5. Build your first app

1. Click **Create App**
2. Describe what you want to build in the chat
3. Follow the workflow — the AI guides you through the build steps
4. Review and promote the generated app when it's ready

In-progress builds stay in **Apps** so you can always pick up where you left off.

This first build is your app's **Genesis Build**; after that, every change you
ask for is a **Revision Run** against the app you already have. See
[Genesis Builds and Revision Runs](getting-started/genesis-builds-and-revision-runs.md).

## Code Context Infrastructure

Mozaiks indexes app source into a `SourceContextBundle`, `AppContextGraph`, and
`AppIntelligenceSnapshot` after generated artifacts, App Intelligence indexing,
or existing-app discovery. This is automatic and uses Tree-sitter-backed parsing
where supported.

For local quickstarts, graph snapshots are stored with Mozaiks artifacts. For
team or production deployments with large repositories, plan to run FalkorDB as
the graph-query mirror for faster multi-hop queries and Studio visualization.
FalkorDB mirrors canonical AppContext and App Intelligence artifacts; it does
not replace them.

!!! tip "Coming back to an existing workspace"

    === "Windows"

        ```powershell
        docker start mozaiks-mongo   # bring MongoDB back up if your PC restarted
        python -m mozaiks studio --dir .\my-workspace --open
        ```

    === "macOS / Linux"

        ```bash
        docker start mozaiks-mongo   # bring MongoDB back up if your machine restarted
        python -m mozaiks studio --dir ./my-workspace --open
        ```

## Troubleshooting

??? "MongoDB connection error"
    Make sure Docker is running and the MongoDB container is up:

    === "Windows"

        ```powershell
        docker start mozaiks-mongo
        ```

    === "macOS / Linux"

        ```bash
        docker start mozaiks-mongo
        ```

    If you used Atlas, double-check the connection string is correct and the
    cluster is reachable.

??? "`mozaiks` is not recognized"
    Use `python -m mozaiks` instead. The `mozaiks` shortcut requires your Python
    scripts directory to be on PATH, which some systems don't configure automatically.

??? "Builds fail or hang"
    Make sure your LLM API key is set in the current shell session. The default
    provider is Google Gemini — set `GEMINI_API_KEY`. If you switched to OpenAI
    or Anthropic, set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` respectively.
    You can open Studio without a key but builds will not run.

---

## What's Next

<div class="grid cards" markdown>

-   :material-lightbulb-outline: **Key Concepts**

    ---

    Workspaces, apps, modules, workflows — the full mental model in one page.

    [:octicons-arrow-right-24: Key Concepts](concepts.md)

-   :material-console: **CLI Reference**

    ---

    Every `mozaiks` command in one place.

    [:octicons-arrow-right-24: CLI Reference](cli-reference.md)

-   :material-view-dashboard: **Use Studio**

    ---

    Learn the workspace and app dashboard surfaces.

    [:octicons-arrow-right-24: Studio](studio/index.md)

-   :material-puzzle-outline: **Guides**

    ---

    Add modules, pages, workflows, integrations, and more.

    [:octicons-arrow-right-24: Guides](guides/index.md)

</div>

---

Contributing to Mozaiks or setting up from a repo checkout? See [Local Setup](local-setup.md).
