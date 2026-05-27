# Getting Started

## Prerequisites

- Python 3.11+
- Node.js 18+
- Docker Desktop — [download here](https://www.docker.com/products/docker-desktop/) (used to run MongoDB)

## 1. Install

```powershell
pip install mozaiks
```

## 2. Start MongoDB

Mozaiks requires a database to store your apps, build history, and workspace
state. The easiest way to start one is with the following command — it runs in
the background, no separate terminal needed:

```powershell
docker run -d --name mozaiks-mongo -p 27017:27017 mongo:7
```

You only need to do this once. If you restart your PC, bring it back with:

```powershell
docker start mozaiks-mongo
```

!!! note "Don't want Docker?"
    Use [MongoDB Atlas](https://www.mongodb.com/atlas) (free tier, cloud-hosted,
    nothing to install). Set `MONGO_URI` to your Atlas connection string in step 3.

## 3. Set environment variables

```powershell
$env:MONGO_URI="mongodb://localhost:27017/mozaiks"
$env:OPENAI_API_KEY="sk-..."
```

!!! note "Other providers"
    Using Atlas? Replace `MONGO_URI` with your Atlas connection string.

    Using Anthropic? Set `ANTHROPIC_API_KEY` instead of `OPENAI_API_KEY`.

## 4. Create your workspace and open Studio

Run this from a parent folder (e.g. your `Desktop` or `Projects` directory).
Replace `my-workspace` with whatever you want to name your app folder:

```powershell
python -m mozaiks quickstart --dir .\my-workspace
```

This scaffolds the workspace, starts the backend and frontend, and opens
Studio in your browser at `http://localhost:3000`.

## 5. Build your first app

1. Click **Create App**
2. Describe what you want to build in the chat
3. Follow the workflow — the AI guides you through the build steps
4. Review and promote the generated app when it's ready

In-progress builds stay in **Apps** so you can always pick up where you left off.

## Coming back to an existing workspace

Run these from the **parent** folder of your workspace (wherever you ran `quickstart`):

```powershell
docker start mozaiks-mongo   # if MongoDB isn't already running
python -m mozaiks studio --dir .\my-workspace --open
```

Replace `.\my-workspace` with the name of the folder that was created during quickstart.

!!! tip "Already inside your workspace folder?"
    If you `cd`'d into the workspace directory first, use `--dir .` instead:

    ```powershell
    python -m mozaiks studio --dir . --open
    ```

## Troubleshooting

??? "MongoDB connection error"
    Make sure Docker is running and the MongoDB container is up:

    ```powershell
    docker start mozaiks-mongo
    ```

    If you used Atlas, double-check the connection string is correct and the
    cluster is reachable.

??? "`mozaiks` is not recognized"
    Use `python -m mozaiks` instead. The `mozaiks` shortcut requires your Python
    scripts directory to be on PATH, which Windows doesn't always do automatically.

??? "Builds fail or hang"
    Make sure `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set in your current
    shell session. You can open Studio without a key but builds will not run.

---

Contributing to Mozaiks or setting up from a repo checkout? See [Local Setup](local-setup.md).
