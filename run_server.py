"""Entry point to run the FastAPI server with Uvicorn.

Adds optional log cleanup so stale handles / large files don't accumulate.
Set CLEAR_LOGS_ON_START=1 to delete existing *.log files before startup.
"""
from __future__ import annotations
import os
from pathlib import Path
import uvicorn

LOG_SUBDIR = Path(__file__).parent / "logs" / "logs"

def _clear_logs():
    if not LOG_SUBDIR.exists():
        return
    for p in LOG_SUBDIR.glob("*.log"):
        try:
            p.unlink()
        except Exception:
            pass  # keep silent; best-effort

if __name__ == "__main__":
    if os.getenv("CLEAR_LOGS_ON_START", "0").lower() in ("1"):
        _clear_logs()

    # Import app only after optional cleanup so logging_config hasn't opened file handles yet
    from shared_app import app  # noqa: WPS433 (import within block is intentional)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
        access_log=True,
        loop="asyncio",
    )