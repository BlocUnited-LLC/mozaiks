"""
Outputs module.

Handles structured outputs and UI tool interactions.
"""

from .structured import (
    agent_has_structured_output,
    get_structured_output_model_fields,
    get_structured_outputs_for_workflow,
)
__all__ = [
    'agent_has_structured_output',
    'get_structured_output_model_fields',
    'get_structured_outputs_for_workflow',
]

