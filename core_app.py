# ==============================================================================
# FILE: core_app.py
# DESCRIPTION: Root-level entry point for the mozaikscore substrate.
#              Mirrors shared_app.py (mozaiksai) in the monorepo structure.
#              Imports the FastAPI app from mozaikscore/core_app.py.
# USAGE: uvicorn core_app:app --host 0.0.0.0 --port 8001 --reload
# ==============================================================================
from mozaikscore.core_app import app  # noqa: F401

__all__ = ["app"]
