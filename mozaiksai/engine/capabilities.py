"""AG2 capability inspection utilities.

This module centralizes runtime feature detection for AG2 so higher layers can
query capabilities without importing vendor internals directly.
"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from typing import Any, Dict


def _has_symbol(module_path: str, symbol_name: str) -> bool:
    try:
        mod = import_module(module_path)
        return hasattr(mod, symbol_name)
    except Exception:
        return False


def _ag2_version() -> str:
    try:
        return version("ag2")
    except PackageNotFoundError:
        return "unknown"


def _supports_tracing_module() -> bool:
    # AG2 0.11.x tracing APIs are exposed from autogen.opentelemetry.
    if find_spec("autogen.opentelemetry") is None:
        return False
    # The AG2 module can exist while opentelemetry deps are missing.
    return find_spec("opentelemetry") is not None


def get_ag2_capability_report() -> Dict[str, Any]:
    """Return a normalized capability report for installed AG2 runtime."""
    run_iter_supported = False
    try:
        from autogen import ConversableAgent
        run_iter_supported = hasattr(ConversableAgent, "run_iter")
    except Exception:
        run_iter_supported = False

    return {
        "engine": "ag2",
        "version": _ag2_version(),
        "events_module": _has_symbol("autogen.events", "BaseEvent"),
        "groupchat_async_run": _has_symbol("autogen.agentchat", "a_run_group_chat"),
        "groupchat_iter_sync": _has_symbol("autogen.agentchat.group.multi_agent_chat", "run_group_chat_iter"),
        "groupchat_iter_async": _has_symbol("autogen.agentchat.group.multi_agent_chat", "a_run_group_chat_iter"),
        "agent_run_iter": run_iter_supported,
        "custom_events": (
            _has_symbol("autogen.events.base_event", "BaseEvent")
            and _has_symbol("autogen.events.base_event", "wrap_event")
            and _has_symbol("autogen.io.base", "IOStream")
        ),
        "runtime_logging": _has_symbol("autogen", "runtime_logging"),
        "opentelemetry": {
            "module_available": find_spec("autogen.opentelemetry") is not None,
            "deps_installed": find_spec("opentelemetry") is not None,
            "enabled": _supports_tracing_module(),
            "instrument_pattern": _has_symbol("autogen.opentelemetry", "instrument_pattern"),
            "instrument_llm_wrapper": _has_symbol("autogen.opentelemetry", "instrument_llm_wrapper"),
        },
    }


__all__ = ["get_ag2_capability_report"]
