"""Workflow validation package facade with lazy re-exports.

We keep the public surface stable while avoiding import-time circular
dependencies between validation tools and structured output helpers.
"""

from importlib import import_module
from typing import TYPE_CHECKING

from .llm_config import PRICE_MAP, clear_llm_caches, get_llm_config

__all__ = [
    "get_llm_config",
    "clear_llm_caches",
    "PRICE_MAP",
    "ValidationOutcome",
    "validate_tool_call",
    "SENTINEL_FLAG",
    "SENTINEL_STATUS",
    "SENTINEL_MESSAGE_KEY",
    "SENTINEL_ERRORS_KEY",
    "SENTINEL_EXPECTED_MODEL_KEY",
    "SENTINEL_AGENT_KEY",
    "SENTINEL_TOOL_KEY",
]

if TYPE_CHECKING:  # pragma: no cover - for type checkers only
    from .tools import (  # noqa: F401
        SENTINEL_AGENT_KEY,
        SENTINEL_ERRORS_KEY,
        SENTINEL_EXPECTED_MODEL_KEY,
        SENTINEL_FLAG,
        SENTINEL_MESSAGE_KEY,
        SENTINEL_STATUS,
        SENTINEL_TOOL_KEY,
        ValidationOutcome,
        validate_tool_call,
    )


_LAZY_EXPORTS = {
    "ValidationOutcome",
    "validate_tool_call",
    "SENTINEL_FLAG",
    "SENTINEL_STATUS",
    "SENTINEL_MESSAGE_KEY",
    "SENTINEL_ERRORS_KEY",
    "SENTINEL_EXPECTED_MODEL_KEY",
    "SENTINEL_AGENT_KEY",
    "SENTINEL_TOOL_KEY",
}


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        module = import_module("mozaiksai.core.workflow.validation.tools")
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(name)

