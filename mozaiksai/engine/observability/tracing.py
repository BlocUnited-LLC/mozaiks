"""Compatibility shim — implementation moved to ``mozaiksai.adapters.ag2.observability.tracing``.

This file re-exports everything from the adapter layer so that existing
code importing from ``mozaiksai.engine.*`` continues to work unchanged.
New code should import directly from the adapter layer.
"""

from __future__ import annotations

from mozaiksai.adapters.ag2.observability.tracing import *  # noqa: F401, F403

__all__ = ['get_tracer_provider', 'instrument_llm_globally', 'instrument_agent', 'instrument_pattern', 'traced_workflow', 'initialize_otel_tracing', 'shutdown_otel_tracing']
