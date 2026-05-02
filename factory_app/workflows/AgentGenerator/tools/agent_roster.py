"""
agent_roster tool - caches agent roster from AgentRosterAgent.

Persists the ordered list of workflow agents so downstream tools
(ToolPlanningAgent, ContextVariablesAgent, ToolsManagerAgent) can read
agent definitions without re-querying MongoDB.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, List, Optional

_logger = logging.getLogger("tools.agent_roster")


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


def agent_roster(
    *,
    AgentRoster: Annotated[Optional[Dict[str, Any]], "AgentRoster payload from AgentRosterAgent"],
    context_variables: Annotated[Optional[Any], "Context variables provided by AG2"] = None,
) -> str:
    """Persist AgentRoster output so downstream agents can read shared context."""

    if not AgentRoster or not isinstance(AgentRoster, dict):
        _logger.warning("agent_roster called with no AgentRoster data")
        return "No AgentRoster payload provided"

    agents: List[Dict[str, Any]] = AgentRoster.get("agents") or []
    if not isinstance(agents, list):
        agents = []

    _cache_context_value(context_variables, "AgentRoster", AgentRoster)

    _logger.info("Cached AgentRoster with %d agents", len(agents))

    agent_names = [
        a.get("agent_name") or a.get("name", "?")
        for a in agents
        if isinstance(a, dict)
    ]
    names_preview = ", ".join(agent_names[:5])
    if len(agent_names) > 5:
        names_preview += f", ... (+{len(agent_names) - 5} more)"

    return f"Cached AgentRoster: {len(agents)} agents — {names_preview}"
