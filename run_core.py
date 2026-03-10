# ==============================================================================
# FILE: run_core.py
# DESCRIPTION: Entry point for the mozaikscore substrate.
#              Runs the FastAPI app on port 8001 via uvicorn.
# USAGE: python run_core.py
# ==============================================================================
import os
import logging
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

if __name__ == "__main__":
    host = os.getenv("CORE_HOST", "0.0.0.0")
    port = int(os.getenv("CORE_PORT", "8001"))
    reload = os.getenv("ENV", "development") == "development"

    uvicorn.run(
        "mozaikscore.core_app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
