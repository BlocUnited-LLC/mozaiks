# === MOZAIKS-CORE-HEADER ===
# FILE: mozaiksai/core/adapters/__init__.py
# DESCRIPTION: Engine adapters — implementations of port protocols.
# ==============================================================================

from .ag2_orchestration import AG2OrchestrationAdapter
from .http_app_backend import HttpAppBackendAdapter, get_app_backend

__all__ = ["AG2OrchestrationAdapter", "HttpAppBackendAdapter", "get_app_backend"]
