# Mozaiks Templates

Two starters — one for each side of the stack.

| Template | What it gives you |
|---|---|
| [`frontend/`](./frontend/README.md) | Vite + React app with branding and workflow UI |
| [`backend/`](./backend/README.md) | FastAPI server + workflow handler skeleton |

## Quick start

```bash
# Frontend
cd templates/frontend
npm install
npm run dev

# Backend (separate terminal)
cd templates/backend
cp .env.example .env
pip install -r requirements.txt
python main.py
```

## What connects them

- Edit **`app.json`** first — it sets `appName`, `appId`, `defaultWorkflow`, and `apiUrl` for the whole stack.
- The workflow **name** (e.g. `hello_world`) must match in both:
  - `app.json` → `"defaultWorkflow": "hello_world"`
  - `backend/workflows/hello_world/__init__.py` → `@workflow("hello_world", ...)`
- The frontend `apiAdapter` uses `app.json`'s `apiUrl` to reach the backend.
