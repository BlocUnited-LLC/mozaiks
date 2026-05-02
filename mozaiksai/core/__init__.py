"""Core Package Root
=====================
Aggregated exports for primary subsystems (workflow, transport, observability, data, events).
Downstream code should prefer importing from specific submodules for clarity, but
these re-exports are provided for convenience and a stable public surface.

Note: all heavy re-exports are **lazy** (loaded on first attribute access) so that
importing `mozaiksai.core.ports.*` or `mozaiksai.core.adapters.*` — which are pure
Python with no AG2 dependencies — does not pull in the AG2 runtime machinery at
collection/import time.
"""
from __future__ import annotations

import importlib
from typing import Any

# Maps exported name → relative submodule that owns it.
_LAZY_EXPORTS: dict[str, str] = {
    # Workflow
    "run_workflow_orchestration": ".workflow",
    "create_ag2_pattern": ".workflow",
    "register_workflow": ".workflow",
    "get_workflow_handler": ".workflow",
    "get_workflow_transport": ".workflow",
    "workflow_status_summary": ".workflow",
    # Transport
    "SimpleTransport": ".transport",
    # Observability
    "PerformanceManager": ".observability",
    "PerformanceConfig": ".observability",
    "get_performance_manager": ".observability",
    # Persistence
    "PersistenceManager": ".data",
    "AG2PersistenceManager": ".data",
    # Events
    "emit_business_event": ".events",
    "emit_tool_call_request": ".events",
    "get_event_dispatcher": ".events",
}


def __getattr__(name: str) -> Any:  # noqa: ANN001
    module_rel = _LAZY_EXPORTS.get(name)
    if module_rel is not None:
        mod = importlib.import_module(module_rel, package=__name__)
        value = getattr(mod, name)
        # Cache in module globals so the next access is a plain dict lookup.
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_LAZY_EXPORTS)
