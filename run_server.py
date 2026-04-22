"""Canonical entry point to run a Mozaiks FastAPI host with Uvicorn.

Adds optional log cleanup so stale handles / large files don't accumulate.
Set CLEAR_LOGS_ON_START=1 to delete existing *.log files before startup.

Select the host with MOZAIKS_HOST:
  - studio   runtime + platform + local/private Studio builder surfaces (default)
  - mozaiks  runtime + platform + Studio + hosted Mozaiks product surfaces
  - runtime  pure workflow/transport substrate
  - platform runtime + app shell/platform hosting
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import uvicorn


LOG_SUBDIR = Path(__file__).parent / "logs" / "logs"
HOST_MODULES = {
    "runtime": "runtime_app",
    "platform": "platform_app",
    "studio": "studio_app",
    "mozaiks": "mozaiks_app",
}


def _clear_logs() -> None:
    if not LOG_SUBDIR.exists():
        return
    for path in LOG_SUBDIR.glob("*.log"):
        try:
            path.unlink()
        except Exception:
            pass  # best-effort cleanup


def _load_app():
    host = os.getenv("MOZAIKS_HOST", "studio").strip().lower() or "studio"
    module_name = HOST_MODULES.get(host)
    if module_name is None:
        valid = ", ".join(sorted(HOST_MODULES))
        raise RuntimeError(f"Invalid MOZAIKS_HOST={host!r}. Expected one of: {valid}")
    module = importlib.import_module(module_name)
    return module.app


if __name__ == "__main__":
    if os.getenv("CLEAR_LOGS_ON_START", "0").lower() in ("1"):
        _clear_logs()

    # Import app only after optional cleanup so logging_config has not opened
    # file handles yet.
    app = _load_app()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
        access_log=True,
        loop="asyncio",
    )
