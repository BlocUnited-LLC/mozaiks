"""shared_app — Thin entry-point that delegates to the application factory.

``run_server.py`` imports ``app`` from this module::

    from shared_app import app

All routes, middleware, lifecycle hooks, and shared state are wired inside
``mozaiksai.transport.factory.build_app()``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on Python path for workflow imports
sys.path.insert(0, str(Path(__file__).parent))

from mozaiksai.transport.factory import build_app  # noqa: E402

app = build_app()
