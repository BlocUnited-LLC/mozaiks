"""Context management package facade.

Re-exports the concrete factories and models used across the runtime so
callers do not need to reach into module-private locations. Only symbols
that actually exist in the submodules are exposed here to avoid import
errors after the package reorganization.
"""

from .adapter import create_context_container
from .schema import (
    ContextAgentView,
    ContextTriggerMatch,
    ContextTriggerSpec,
    ContextVariableDefinition,
    ContextVariableSource,
    ContextVariablesPlan,
    load_context_variables_config,
)
from .variables import _create_minimal_context, _load_context_async
from .derived import DerivedContextManager

# Provide nicer aliases without the leading underscore while keeping the
# original names available for existing call sites.
create_minimal_context = _create_minimal_context
load_context_async = _load_context_async

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

