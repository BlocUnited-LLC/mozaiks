# === MOZAIKS-CORE-HEADER ===
# FILE: core/adapters/__init__.py
# DESCRIPTION: Engine adapters — implementations of port protocols.
# ==============================================================================

from .ag2_orchestration import AG2OrchestrationAdapter
from .core_client import CoreServiceClient

__all__ = ["AG2OrchestrationAdapter", "CoreServiceClient"]
