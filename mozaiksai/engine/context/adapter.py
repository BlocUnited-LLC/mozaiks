"""Compatibility shim — implementation moved to ``mozaiksai.adapters.ag2.context_adapter``.

This file re-exports everything from the adapter layer so that existing
code importing from ``mozaiksai.engine.*`` continues to work unchanged.
New code should import directly from the adapter layer.
"""

from __future__ import annotations

from mozaiksai.adapters.ag2.context_adapter import *  # noqa: F401, F403

__all__ = ['create_context_container']
