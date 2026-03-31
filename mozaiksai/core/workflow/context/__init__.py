"""Context management package facade.

Re-exports the concrete factories and models used across the runtime so
callers do not need to reach into module-private locations. Only symbols
that actually exist in the submodules are exposed here to avoid import
errors after the package reorganization.
"""

from .adapter import create_context_container
from .derived import DerivedContextManager
from .schema import (
    ContextAgentView,
    ContextTriggerMatch,
    ContextTriggerSpec,
    ContextVariableDefinition,
    ContextVariableSource,
    ContextVariablesPlan,
    load_context_variables_config,
)


def _create_minimal_context(workflow_name: str, app_id):  # type: ignore[no-untyped-def]
    from .variables import _create_minimal_context as _impl

    return _impl(workflow_name, app_id)


async def _load_context_async(workflow_name: str, app_id):  # type: ignore[no-untyped-def]
    from .variables import _load_context_async as _impl

    return await _impl(workflow_name, app_id)


def create_minimal_context(workflow_name: str, app_id):  # type: ignore[no-untyped-def]
    return _create_minimal_context(workflow_name, app_id)


async def load_context_async(workflow_name: str, app_id):  # type: ignore[no-untyped-def]
    return await _load_context_async(workflow_name, app_id)

__all__ = [
    "create_context_container",
    "ContextAgentView",
    "ContextTriggerMatch",
    "ContextTriggerSpec",
    "ContextVariableDefinition",
    "ContextVariableSource",
    "ContextVariablesPlan",
    "DerivedContextManager",
    "load_context_variables_config",
    "_create_minimal_context",
    "_load_context_async",
    "create_minimal_context",
    "load_context_async",
]
