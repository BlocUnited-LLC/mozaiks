"""
Backend entry point.

The only thing you need to do to register a new workflow:
  1. Create a subfolder under workflows/
  2. Add an __init__.py that uses the @workflow decorator

Everything in this file runs as-is. No manual registration.
"""
from __future__ import annotations

import importlib
import os
import pkgutil

import uvicorn

# ── Auto-discover workflows ───────────────────────────────────────────────────
# Imports every submodule under workflows/, which triggers each module's
# @workflow decorator and adds it to the registry before the app starts.
import workflows as _wf_pkg

for _finder, _name, _ispkg in pkgutil.walk_packages(_wf_pkg.__path__, prefix="workflows."):
    importlib.import_module(_name)

# ── Start server ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "true").lower() == "true"

    uvicorn.run(
        "mozaiks.core.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
        reload=reload,
    )
