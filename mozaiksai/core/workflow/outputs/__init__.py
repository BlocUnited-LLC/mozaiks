"""
Outputs module.

Handles structured outputs and UI tool interactions.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "emit_validated_agent_output": ".runtime_events",
    "reply_body_to_data": ".runtime_validation",
    "validate_agent_structured_output": ".runtime_validation",
    "agent_has_structured_output": ".structured",
    "get_provider_response_model": ".structured",
    "get_structured_output_model_fields": ".structured",
    "get_structured_outputs_for_workflow": ".structured",
}


def __getattr__(name: str) -> Any:
    if name == "structured":
        module = import_module(".structured", __name__)
        globals()[name] = module
        return module
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value

__all__ = [
    "agent_has_structured_output",
    "emit_validated_agent_output",
    "get_provider_response_model",
    "get_structured_output_model_fields",
    "get_structured_outputs_for_workflow",
    "reply_body_to_data",
    "structured",
    "validate_agent_structured_output",
]

