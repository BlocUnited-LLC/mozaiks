"""Assemble one workflow's parsed configuration from its modular YAML files.

Shared by the workflow manager, which reads the files from disk, and by callers
that must accept or refuse a workflow before its files exist there.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import yaml

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
)

_SECTION_PARSERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "agents": parse_agents_config,
    "transition_graph": parse_transition_graph_config,
    "context_variables": parse_context_variables_config,
    "structured_outputs": parse_structured_outputs_config,
    "middleware": parse_middleware_config,
    "a2a": parse_a2a_config,
}


def parse_workflow_mapping(text: str, label: str) -> dict[str, Any]:
    """One workflow YAML document; an empty file is an empty mapping."""
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{label} must contain a mapping at root")
    return data


def assemble_workflow_config(
    workflow_name: str, read_section: Callable[[str], dict[str, Any] | None],
) -> dict[str, Any]:
    """Parse and merge a workflow's files.

    ``read_section`` returns the raw mapping of ``<section>.yaml``, or ``None``
    when the workflow has no such file.

    Canonical files: orchestrator.yaml, agents.yaml, transition_graph.yaml,
    context_variables.yaml, structured_outputs.yaml, tools.yaml,
    ui_config.yaml, middleware.yaml.
    Optional file: a2a.yaml.
    Tools file is expected to expose a unified 'tools' list.

    Top-level merge rules:
      - orchestrator keys merged at root
      - explicit sections under their key (agents, transition_graph, middleware, context_variables, structured_outputs)
      - tools contributes its root keys ('tools' and 'lifecycle_tools') without transformation
      - ui_config contributes its root keys
    """
    config: dict[str, Any] = {}

    # Orchestrator (top-level keys)
    orchestrator_raw = read_section("orchestrator")
    if orchestrator_raw is None:
        raise ValueError(f"Workflow '{workflow_name}' is missing required file: orchestrator.yaml")
    config.update(parse_orchestrator_config(orchestrator_raw))

    # Sectioned files
    for section_name, parser in _SECTION_PARSERS.items():
        raw_data = read_section(section_name)
        if raw_data is not None:
            config[section_name] = parser(raw_data)

    # Tools file (canonical unified list under 'tools')
    tools_raw = read_section("tools")
    if tools_raw is not None:
        tools_data = parse_tools_config(tools_raw)
        config["tools"] = tools_data.get("tools", [])
        config["lifecycle_tools"] = tools_data.get("lifecycle_tools", [])

    # UI config
    ui_raw = read_section("ui_config")
    if ui_raw is not None:
        # Merge UI config keys at root (e.g., visual_agents)
        config.update(parse_ui_config(ui_raw))

    return config


__all__ = ["assemble_workflow_config", "parse_workflow_mapping"]
