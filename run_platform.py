from __future__ import annotations

import uvicorn

from platform_app import app


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
