"""AG2 (AutoGen 2) engine adapter for Mozaiks runtime.

This package contains all AG2-specific code, isolating the AG2 framework
dependency from the runtime core. All AG2 imports should be contained
within this package.

Structure:
    adapter.py          - Main AG2EngineAdapter implementation
    executor.py         - GroupChat execution logic
    agent_factory.py    - ConversableAgent creation
    pattern_factory.py  - AG2 pattern selection (Auto, Default, etc.)
    handoff_builder.py  - Handoff configuration
    event_translator.py - AG2 event → DomainEvent translation
    iostream_bridge.py  - IOStream for streaming
    context_adapter.py  - ContextVariables handling
    llm_config.py       - LLM configuration
    capabilities.py     - AG2 feature detection
    message_utils.py    - Message utilities

    observability/      - AG2-specific logging and tracing
    a2a/                - Agent-to-agent protocol
"""

from __future__ import annotations

# Imports are intentionally NOT at the package level to avoid circular imports.
# Import directly from sub-modules:
#   from mozaiksai.adapters.ag2.adapter import AG2EngineAdapter
#   from mozaiksai.adapters.ag2.executor import GroupChatExecutor

__all__ = [
    "AG2EngineAdapter",
    "GroupChatExecutor",
    "PreparedRun",
]


def __getattr__(name: str) -> object:
    """Lazy attribute access to prevent circular imports at package init time."""
    if name in ("AG2EngineAdapter", "GroupChatExecutor", "PreparedRun"):
        from mozaiksai.adapters.ag2 import adapter as _adapter_mod
        from mozaiksai.engine.executor.groupchat_executor import (
            GroupChatExecutor as _GCE,
            PreparedRun as _PR,
        )
        _map = {
            "AG2EngineAdapter": _adapter_mod.AG2EngineAdapter,
            "GroupChatExecutor": _GCE,
            "PreparedRun": _PR,
        }
        return _map[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
