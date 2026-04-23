from __future__ import annotations

"""Run the canonical runtime substrate host directly."""

import uvicorn

from runtime_app import app


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
        access_log=True,
        loop="asyncio",
    )
