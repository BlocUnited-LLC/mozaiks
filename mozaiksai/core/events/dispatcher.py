"""
dispatcher.py — Re-export shim so tests and external code can import
UnifiedEventDispatcher from mozaiksai.core.events.dispatcher.
"""

from mozaiksai.core.events.unified_event_dispatcher import UnifiedEventDispatcher  # noqa: F401

__all__ = ["UnifiedEventDispatcher"]
