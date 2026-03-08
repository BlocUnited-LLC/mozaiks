"""Compatibility shim — implementation moved to ``mozaiksai.adapters.ag2.context_utils``.

This file re-exports everything from the adapter layer so that existing
code importing from ``mozaiksai.engine.*`` continues to work unchanged.
New code should import directly from the adapter layer.
"""

from __future__ import annotations

from mozaiksai.adapters.ag2.context_utils import *  # noqa: F401, F403

__all__ = ['context_to_dict', 'stringify_context_value', 'format_template', 'render_exposure_fragment', 'merge_message_parts', 'apply_context_exposures', 'render_default_context_fragment', 'build_exposure_update_hook']
