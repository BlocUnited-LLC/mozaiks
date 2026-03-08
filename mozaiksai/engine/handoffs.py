"""Compatibility shim — implementation moved to ``mozaiksai.adapters.ag2.handoff_builder``.

This file re-exports everything from the adapter layer so that existing
code importing from ``mozaiksai.engine.*`` continues to work unchanged.
New code should import directly from the adapter layer.
"""

from __future__ import annotations

from mozaiksai.adapters.ag2.handoff_builder import *  # noqa: F401, F403

__all__ = ['wire_handoffs', 'wire_handoffs_with_debugging', 'handoff_manager', 'HandoffManager']
