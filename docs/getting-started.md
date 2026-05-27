# Getting Started

## Prerequisites

- Python 3.11+
- Node.js 18+
- MongoDB — [Atlas free tier](https://www.mongodb.com/atlas) or local MongoDB running on your machine

## 1. Install

```powershell
pip install mozaiks
```

## 2. Set environment variables

```powershell
$env:MONGO_URI="mongodb://localhost:27017/mozaiks"
$env:OPENAI_API_KEY="sk-..."
```

!!! note "Other providers"
    Using Atlas? Replace `MONGO_URI` with your Atlas connection string.

    Using Anthropic? Set `ANTHROPIC_API_KEY` instead of `OPENAI_API_KEY`.

## 3. Create your workspace and open Studio

```powershell
python -m mozaiks quickstart --dir .\my-workspace
```

This scaffolds the workspace, starts the backend and frontend, and opens
Studio in your browser at `http://localhost:3000`.

## 4. Build your first app

1. Click **Create App**
2. Describe what you want to build in the chat
3. Follow the workflow — the AI guides you through the build steps
4. Review and promote the generated app when it's ready

In-progress builds stay in **Apps** so you can always pick up where you left off.

## Coming back to an existing workspace

```powershell
python -m mozaiks studio --dir .\my-workspace --open
```

## Troubleshooting

??? "`mozaiks` is not recognized"
    Use `python -m mozaiks` instead. The `mozaiks` shortcut requires your Python
    scripts directory to be on PATH, which Windows doesn't always do automatically.

??? "MongoDB connection error"
    Make sure local MongoDB is running, or double-check your Atlas connection string
    is correct and the cluster is reachable.

??? "Builds fail or hang"
    Make sure `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set in your current
    shell session. You can open Studio without a key but builds will not run.

---

Contributing to Mozaiks or setting up from a repo checkout? See [Local Setup](local-setup.md).
