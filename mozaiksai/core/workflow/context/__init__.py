"""Context management package facade.

Re-exports the concrete factories and models used across the runtime so
callers do not need to reach into module-private locations. Only symbols
that actually exist in the submodules are exposed here to avoid import
errors after the package reorganization.
"""

from __future__ import annotations

from typing import Any

from .adapter import create_context_container
from .authority import (
    AGENT_TEXT_WRITER,
    CALLER_INPUT_WRITER,
    CONTEXT_AUTHORITY_WRITER_DEP,
    CONTEXT_BRIDGE_WRITER,
    DETERMINISTIC_TOOL_WRITER,
    LIFECYCLE_TOOL_WRITER,
    LIVE_USER_CONTEXT_WRITER,
    PERSISTED_REPLAY_WRITER,
    RUNTIME_SYSTEM_WRITER,
    STRUCTURED_OUTPUT_WRITER,
    TASK_BATCH_WRITER,
    TOOL_WRITEBACK_WRITER,
    TRANSITION_ROUTER_WRITER,
    UI_RESPONSE_TRIGGER_WRITER,
    USER_TEXT_TRIGGER_WRITER,
    ContextAuthorityClass,
    ContextAuthorityError,
    ContextAuthorityPolicy,
    ContextVariableAuthority,
    ScopedContextWriter,
    build_context_authority_policy,
    validate_transition_context_authority,
)
from .schema import (
    ContextAgentView,
    ContextTriggerMatch,
    ContextTriggerSpec,
    ContextVariableDefinition,
    ContextVariableSource,
    ContextVariablesPlan,
    load_context_variables_config,
)


def __getattr__(name: str) -> Any:
    if name == "DerivedContextManager":
        from .derived import DerivedContextManager

        globals()[name] = DerivedContextManager
        return DerivedContextManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    "AGENT_TEXT_WRITER",
    "CALLER_INPUT_WRITER",
    "CONTEXT_AUTHORITY_WRITER_DEP",
    "CONTEXT_BRIDGE_WRITER",
    "DETERMINISTIC_TOOL_WRITER",
    "LIFECYCLE_TOOL_WRITER",
    "LIVE_USER_CONTEXT_WRITER",
    "PERSISTED_REPLAY_WRITER",
    "RUNTIME_SYSTEM_WRITER",
    "STRUCTURED_OUTPUT_WRITER",
    "TASK_BATCH_WRITER",
    "TOOL_WRITEBACK_WRITER",
    "TRANSITION_ROUTER_WRITER",
    "UI_RESPONSE_TRIGGER_WRITER",
    "USER_TEXT_TRIGGER_WRITER",
    "ContextAuthorityClass",
    "ContextAuthorityError",
    "ContextAuthorityPolicy",
    "ContextVariableAuthority",
    "ScopedContextWriter",
    "build_context_authority_policy",
    "validate_transition_context_authority",
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
