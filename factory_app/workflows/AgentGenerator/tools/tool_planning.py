"""
tool_planning tool - caches tool and UX planning from ToolPlanningAgent.

Persists agent tools, lifecycle tools, system hooks, and UI requirements
so ToolsManagerAgent and downstream agents can access the full planning
output without re-querying MongoDB.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, List, Optional

from mozaiksai.core.workflow.ui_primitives import (
    get_component_ui_primitive_names,
    get_page_ui_primitive_names,
    validate_component_ui_primitives,
)
from mozaiksai.core.workflow.workflow_ui_catalog import (
    get_workflow_shipped_component_names,
    get_workflow_shipped_component_map,
    get_workflow_ui_primitive_ids,
    validate_workflow_ui_primitive_ids,
)

_logger = logging.getLogger("tools.tool_planning")


def _cache_context_value(context_variables: Any, key: str, value: Any) -> None:
    if not context_variables:
        return
    try:
        setter = getattr(context_variables, "set", None)
        if callable(setter):
            setter(key, value)
            return
    except Exception as exc:
        _logger.debug("Unable to cache %s via context_variables.set: %s", key, exc)

    try:
        data = getattr(context_variables, "data", None)
        if isinstance(data, dict):
            data[key] = value
    except Exception as exc:
        _logger.debug("Unable to cache %s via context_variables.data: %s", key, exc)


def tool_planning(
    *,
    ToolPlanning: Annotated[Optional[Dict[str, Any]], "ToolPlanning payload from ToolPlanningAgent"],
    context_variables: Annotated[Optional[Any], "Context variables provided by AG2"] = None,
) -> str:
    """Submit tool planning specifications that merge with workflow stage strategy."""

    if not ToolPlanning or not isinstance(ToolPlanning, dict):
        _logger.warning("tool_planning called with no ToolPlanning data")
        return "No ToolPlanning payload provided"

    agent_tools: List[Any] = ToolPlanning.get("agent_tools") or []
    lifecycle_tools: List[Any] = ToolPlanning.get("lifecycle_tools") or []
    system_hooks: List[Any] = ToolPlanning.get("system_hooks") or []
    ui_requirements: List[Any] = ToolPlanning.get("ui_requirements") or []

    if not isinstance(agent_tools, list):
        agent_tools = []
    if not isinstance(lifecycle_tools, list):
        lifecycle_tools = []
    if not isinstance(system_hooks, list):
        system_hooks = []
    if not isinstance(ui_requirements, list):
        ui_requirements = []

    normalized_ui_requirements: List[Dict[str, Any]] = []
    shipped_component_map = dict(get_workflow_shipped_component_map())
    for index, requirement in enumerate(ui_requirements):
        if not isinstance(requirement, dict):
            raise ValueError(
                f"ToolPlanning.ui_requirements[{index}] must be an object, "
                f"got {type(requirement).__name__}"
            )
        normalized_requirement = dict(requirement)
        primitive_id = normalized_requirement.get("workflow_primitive")
        normalized_requirement["workflow_primitive"] = (
            validate_workflow_ui_primitive_ids(
                [primitive_id] if primitive_id is not None else None,
                context=f"ToolPlanning.ui_requirements[{index}].workflow_primitive",
            )[0]
            if primitive_id is not None
            else "composer_reply"
        )
        normalized_requirement["primitives_hint"] = validate_component_ui_primitives(
            normalized_requirement.get("primitives_hint"),
            context=f"ToolPlanning.ui_requirements[{index}].primitives_hint",
        )
        if normalized_requirement["workflow_primitive"] == "composer_reply":
            normalized_requirement["component"] = None
            normalized_requirement["display"] = "composer"
            normalized_requirement["primitives_hint"] = []
        else:
            shipped_component = shipped_component_map.get(normalized_requirement["workflow_primitive"])
            component_name = str(normalized_requirement.get("component") or "").strip()
            if shipped_component and not component_name:
                normalized_requirement["component"] = shipped_component
                component_name = shipped_component
            if shipped_component and component_name == shipped_component:
                normalized_requirement["primitives_hint"] = []
        normalized_ui_requirements.append(normalized_requirement)

    normalized_payload = dict(ToolPlanning)
    normalized_payload["ui_requirements"] = normalized_ui_requirements

    _cache_context_value(context_variables, "ToolPlanning", normalized_payload)
    _cache_context_value(
        context_variables,
        "available_ui_primitives",
        list(get_component_ui_primitive_names()),
    )
    _cache_context_value(
        context_variables,
        "available_page_primitives",
        list(get_page_ui_primitive_names()),
    )
    _cache_context_value(
        context_variables,
        "available_workflow_ui_primitives",
        list(get_workflow_ui_primitive_ids(include_shell_status=False)),
    )
    _cache_context_value(
        context_variables,
        "available_shipped_workflow_components",
        list(get_workflow_shipped_component_names()),
    )

    _logger.info(
        "Cached ToolPlanning: %d agent tools, %d lifecycle tools, %d hooks, %d UI requirements",
        len(agent_tools),
        len(lifecycle_tools),
        len(system_hooks),
        len(normalized_ui_requirements),
    )

    return (
        f"Tool planning cached: "
        f"{len(agent_tools)} agent tools, "
        f"{len(lifecycle_tools)} lifecycle tools, "
        f"{len(system_hooks)} hooks, "
        f"{len(normalized_ui_requirements)} UI requirements"
    )
