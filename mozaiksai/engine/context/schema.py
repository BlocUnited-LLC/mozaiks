"""Compatibility shim - implementation moved to ``mozaiksai.adapters.ag2.schema``."""
from __future__ import annotations
from mozaiksai.adapters.ag2.schema import *  # noqa: F401, F403
__all__ = [
    "ContextTriggerMatch",
    "ContextTriggerSpec",
    "ContextVariableSource",
    "ContextVariableDefinition",
    "ContextAgentView",
    "ContextVariablesPlan",
    "load_context_variables_config",
]
