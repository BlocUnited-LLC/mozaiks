"""Compatibility shim — implementation moved to ``mozaiksai.adapters.ag2.a2a.remote``.

This file re-exports everything from the adapter layer so that existing
code importing from ``mozaiksai.engine.*`` continues to work unchanged.
New code should import directly from the adapter layer.
"""

from __future__ import annotations

from mozaiksai.adapters.ag2.a2a.remote import *  # noqa: F401, F403
