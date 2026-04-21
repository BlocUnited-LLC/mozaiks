#!/usr/bin/env python3
"""
Test server for mozaiksai workflows.

Run with:
    python run_test.py

Then:
    - Health check: http://localhost:8000/ai/health
    - List workflows: http://localhost:8000/ai/workflows
    - WebSocket: ws://localhost:8000/ai/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}
"""

import os
import json
import uuid
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Set workflow directory before importing mozaiksai
WORKFLOW_DIR = os.path.join(os.path.dirname(__file__), "platform", "workflows")
CONFIG_DIR = os.path.join(os.path.dirname(__file__), "platform", "config")
os.environ["MOZAIKS_WORKFLOWS_PATH"] = WORKFLOW_DIR

# Disable auth for demo mode - allows WebSocket connections without JWT tokens
os.environ["AUTH_ENABLED"] = "false"

from mozaiksai import create_mozaiks_app

# Helper to load JSON config files
def load_config(filename: str, default=None):
    path = os.path.join(CONFIG_DIR, filename)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default or {}

# Create main app
app = FastAPI(
    title="Mozaiks Test Server",
    description="Test server for mozaiksai workflows",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create the mozaiks AI app
mozaiks_app = create_mozaiks_app(
    workflow_dir=WORKFLOW_DIR,
    debug=True,
)

# Mount mozaiksai at /ai prefix
app.mount("/ai", mozaiks_app)

# Also mount at root for WebSocket compatibility (frontend expects /ws/...)
# This needs to be AFTER all @app routes are defined, so we do it at the end

# Root health check
@app.get("/")
async def root():
    return {
        "service": "mozaiks-test",
        "ai_runtime": "/ai",
        "docs": "/ai/docs",
        "workflows": "/ai/workflows",
        "health": "/ai/health",
    }

# ============================================================================
# Config API endpoints (required by chat-ui frontend)
# ============================================================================

@app.get("/api/theme-config")
async def theme_config():
    """Return theme configuration."""
    return load_config("theme_config.json")

@app.get("/api/shell-config")
async def shell_config():
    """Return shell/app configuration derived from ai.json."""
    ai = load_config("ai.json")
    chat = ai.get("chat") or {}
    workflows = ai.get("workflows") or {}
    return {
        "chat_startup_mode": chat.get("chat_startup_mode") or chat.get("startup_mode") or "ask",
        "entry_point": workflows.get("entry_point"),
        "resume_policy": workflows.get("resume_policy"),
    }

@app.get("/api/themes/{theme_id}")
async def get_theme(theme_id: str):
    """Return a specific theme by ID."""
    theme_config = load_config("theme_config.json")
    themes = theme_config.get("available_themes", [])
    for theme in themes:
        if theme.get("id") == theme_id:
            return theme
    # Return default theme colors if not found
    return {
        "id": theme_id,
        "name": theme_id.capitalize(),
        "colors": theme_config.get("colors", {})
    }

# ============================================================================
# Workflow/Chat API stubs (minimal endpoints for frontend compatibility)
# ============================================================================

@app.get("/api/workflows")
async def list_workflows():
    """List available workflows from the workflow directory."""
    workflows = []
    if os.path.isdir(WORKFLOW_DIR):
        for name in os.listdir(WORKFLOW_DIR):
            workflow_path = os.path.join(WORKFLOW_DIR, name)
            if os.path.isdir(workflow_path):
                # Load orchestrator.yaml if available
                orch_path = os.path.join(workflow_path, "orchestrator.yaml")
                if os.path.exists(orch_path):
                    workflows.append({
                        "name": name,
                        "displayName": name.replace("_", " ").title(),
                        "description": f"{name} workflow",
                    })
    return workflows

@app.get("/api/workflows/{workflow_name}/transport")
async def workflow_transport(workflow_name: str):
    """Return transport configuration for a workflow."""
    return {
        "type": "websocket",
        "wsUrl": f"ws://localhost:8000/ai/ws/{workflow_name}",
    }

@app.get("/api/sessions/list/{app_id}/{user_id}")
async def list_sessions(app_id: str, user_id: str):
    """Return workflow sessions for a user (empty for demo)."""
    return []

@app.get("/api/general_chats/list/{app_id}/{user_id}")
async def list_general_chats(app_id: str, user_id: str, limit: int = 50):
    """Return general chat sessions for a user (empty for demo)."""
    return []

@app.get("/api/chats/exists/{app_id}/{workflow_name}/{chat_id}")
async def chat_exists(app_id: str, workflow_name: str, chat_id: str):
    """Check if a chat exists (always false for demo)."""
    return {"exists": False}

@app.post("/api/chats/{app_id}/{workflow_name}/start")
async def start_chat(app_id: str, workflow_name: str):
    """Start a new chat session."""
    chat_id = str(uuid.uuid4())
    return {
        "chat_id": chat_id,
        "workflow_name": workflow_name,
        "app_id": app_id,
        "status": "created",
    }

@app.get("/api/sessions/oldest/{app_id}/{user_id}")
async def oldest_session(app_id: str, user_id: str):
    """Return oldest resumable session (none for demo)."""
    return None

@app.get("/api/sessions/last_active/{app_id}/{user_id}")
async def last_active_session(app_id: str, user_id: str):
    """Return last active session (none for demo)."""
    return None

# Note: WebSocket connections must use /ai/ws/{workflow}/ path
# The frontend should use the wsUrl from /api/workflows/{name}/transport


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("  Mozaiks Test Server")
    print(f"{'='*60}")
    print(f"  Workflow directory: {WORKFLOW_DIR}")
    print(f"  API docs: http://localhost:8000/ai/docs")
    print(f"  Health: http://localhost:8000/ai/health")
    print(f"  Workflows: http://localhost:8000/ai/workflows")
    print(f"{'='*60}\n")

    uvicorn.run(
        "run_test:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
