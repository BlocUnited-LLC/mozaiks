# Mozaiks Backend Template

A minimal Python/FastAPI backend wired to the mozaiks runtime.

## Structure

```
backend/
  workflows/
    hello_world/     ← copy this to add a workflow
      __init__.py    ← your workflow handler
  main.py            ← entry point, auto-discovers workflows
  requirements.txt
  .env.example
```

**You only need to touch `workflows/`.**
Every subfolder with an `__init__.py` that uses `@workflow` is picked up automatically.

## Setup

```bash
cp .env.example .env
pip install -r requirements.txt
python main.py
```

API runs at `http://localhost:8000`.

## Adding a workflow

1. Copy `workflows/hello_world/` to `workflows/your_workflow_name/`
2. Update the `@workflow` name and handler logic
3. Restart the server — it auto-discovers the new module

The workflow name must match the `defaultWorkflow` set in `templates/frontend/App.jsx`.
