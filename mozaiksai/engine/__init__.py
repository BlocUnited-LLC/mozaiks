"""Layer 1 — Engine: AG2-specific execution layer.

This package is the ONLY place in the system that may import from ``autogen``.
Higher layers interact with the engine exclusively through
``mozaiksai.ports.OrchestrationPort`` and ``mozaiksai.contracts.DomainEvent``.

MIGRATION NOTE
--------------
AG2-specific code is being moved to ``mozaiksai.adapters.ag2``. This package
purpose is the AG2 execution layer containing orchestration, streaming, and
context management.

New code should import directly from the adapters layer:

    from mozaiksai.adapters.ag2.adapter import AG2EngineAdapter
    from mozaiksai.engine.executor.groupchat_executor import GroupChatExecutor
"""
