# === MOZAIKS-CORE-HEADER ===
# FILE: core/adapters/__init__.py
# DESCRIPTION: Engine adapters — AG2-specific implementations of port protocols.
# ==============================================================================

from .ag2_orchestration import AG2OrchestrationAdapter

__all__ = ["AG2OrchestrationAdapter"]
