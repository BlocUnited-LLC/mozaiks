"""Runtime run-registry health summary.

Provides a lightweight snapshot of active runs for the health endpoint.
Intentionally simple — the runtime does not maintain a detailed in-memory
run registry at present.
"""
from __future__ import annotations

from typing import Any, Dict


def get_run_registry_summary() -> Dict[str, Any]:
    """Return a snapshot of the active-run registry for health checks."""
    return {
        "active_count": 0,
        "total_runs": 0,
        "runs": [],
        "note": "Registry tracking disabled for simplicity",
    }


__all__ = ["get_run_registry_summary"]
