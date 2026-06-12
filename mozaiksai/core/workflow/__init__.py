"""Workflow Management Package.

Public entry points stay stable, but imports are resolved lazily so that
submodules like startup_messages can load without eagerly importing the
orchestration engine and re-entering persistence during runtime startup.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "run_workflow_orchestration": ".orchestration_patterns",
    "register_workflow": ".workflow_manager",
    "get_workflow_handler": ".workflow_manager",
    "get_workflow_transport": ".workflow_manager",
    "workflow_status_summary": ".workflow_manager",
    "get_workflow_tools": ".workflow_manager",
}

# Subpackages exposed as lazy attributes so dotted imports like
# ``mozaiksai.core.workflow.artifacts.get_review_artifact`` work without
# eagerly loading any orchestration internals at startup.
_SUBPACKAGES = {"artifacts"}

__all__ = list(_EXPORTS) + list(_SUBPACKAGES)


def __getattr__(name: str) -> Any:
    if name in _SUBPACKAGES:
        module = import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_path, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

