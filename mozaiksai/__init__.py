# ==============================================================================
# MozaiksAI Runtime — Top-level Namespace
# ==============================================================================
"""
MozaiksAI runtime namespace package.

Layered architecture:

    Layer 0 — Contracts & Ports (engine-agnostic interfaces)
        from mozaiksai.contracts import DomainEvent, RunRequest
        from mozaiksai.ports import OrchestrationPort

    Layer 1 — Engine Adapter (AG2-specific execution)
        from mozaiksai.engine import run_workflow_orchestration

    Layer 1.5 — Kernel / Orchestration (AG2-free coordination)
        from mozaiksai.kernel import UniversalOrchestrator

    Layer 2 — Runtime Services (persistence, auth, config)
        from mozaiksai.runtime.auth import require_user_scope
        from mozaiksai.runtime.data import AG2PersistenceManager

    Layer 3 — Transport (HTTP + WebSocket)
        from mozaiksai.transport.websocket.handler import SimpleTransport
"""

__version__ = "1.0.0"
