"""Kernel events sub-package — event dispatch, handoff, and usage tracking.

Use explicit imports to avoid triggering the AG2 import chain:

    from mozaiksai.kernel.dispatcher import UnifiedEventDispatcher, get_event_dispatcher
    from mozaiksai.kernel.handoff_events import emit_handoff_event
"""
