from __future__ import annotations

from typing import Any

_HEADER = "[TASK BATCH ROUTE CONTEXT]"


def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _update_section(agent: Any, body: str) -> None:
    current = (
        getattr(agent, "system_message", None)
        or getattr(agent, "_system_message", "")
        or ""
    )
    section = f"{_HEADER}\n{body}"
    if _HEADER in current:
        pre, _, rest = current.partition(_HEADER)
        next_section_idx = rest.find("\n\n[")
        after = rest[next_section_idx:] if next_section_idx > 0 else ""
        new_message = pre.rstrip() + "\n\n" + section + after
    else:
        new_message = current.rstrip() + "\n\n" + section

    updater = getattr(agent, "update_system_message", None)
    if callable(updater):
        updater(new_message)
    else:
        agent.system_message = new_message
        agent._system_message = new_message


def inject_task_batch_route_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    del messages
    context_variables = getattr(agent, "context_variables", None)
    route_ready = bool(_context_get(context_variables, "task_batch_route_ready", True))
    route_mode = str(_context_get(context_variables, "task_batch_route_mode", "task_batch"))

    body = (
        "Route context for this smoke run:\n"
        f"- task_batch_route_ready: {route_ready}\n"
        f"- task_batch_route_mode: {route_mode}\n"
        "Plan conveyor work units only when route_ready is true and route_mode is task_batch."
    )
    _update_section(agent, body)
