"""Declarative workflow contracts.

This package defines strict Pydantic-backed contracts for workflow YAML files
and exposes parser helpers that return validated, canonical dictionaries.
"""

from .assembly import assemble_workflow_config, parse_workflow_mapping
from .contracts import (
    parse_a2a_config,
    parse_agents_config,
    parse_context_variables_config,
    parse_middleware_config,
    parse_orchestrator_config,
    parse_structured_outputs_config,
    parse_tools_config,
    parse_transition_graph_config,
    parse_ui_config,
    validate_orchestrator_contract,
)

__all__ = [
    "assemble_workflow_config",
    "parse_workflow_mapping",
    "parse_orchestrator_config",
    "parse_agents_config",
    "parse_transition_graph_config",
    "parse_context_variables_config",
    "parse_structured_outputs_config",
    "parse_tools_config",
    "parse_ui_config",
    "parse_middleware_config",
    "parse_a2a_config",
    "validate_orchestrator_contract",
]
