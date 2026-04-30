"""
iterator_tool - lifecycle controller for multi-workflow pack generation.

Runs after OrchestratorAgent completes each workflow. Advances
current_workflow_index and sets pack_generation_complete when all
workflows in workflows_spec have been generated.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, List, Optional

_logger = logging.getLogger("tools.iterator_tool")


def _cache(context_variables: Any, key: str, value: Any) -> None:
    if not context_variables:
        return
    try:
        setter = getattr(context_variables, "set", None)
        if callable(setter):
            setter(key, value)
    except Exception as exc:
        _logger.debug("Unable to set %s: %s", key, exc)


def _get(context_variables: Any, key: str, default: Any = None) -> Any:
    if not context_variables:
        return default
    try:
        getter = getattr(context_variables, "get", None)
        if callable(getter):
            return getter(key, default)
        data = getattr(context_variables, "data", None)
        if isinstance(data, dict):
            return data.get(key, default)
    except Exception:
        pass
    return default


def iterator_check(
    context_variables: Annotated[Optional[Any], "Context variables provided by AG2"] = None,
) -> str:
    """Advance the pack iteration counter; set pack_generation_complete when done."""

    workflows_spec: List[Any] = _get(context_variables, "workflows_spec", [])
    if not isinstance(workflows_spec, list):
        workflows_spec = []

    current_index: int = _get(context_variables, "current_workflow_index", 0)
    if not isinstance(current_index, int):
        current_index = 0

    next_index = current_index + 1
    total = len(workflows_spec)
    complete = next_index >= total

    _cache(context_variables, "current_workflow_index", next_index)
    _cache(context_variables, "pack_generation_complete", complete)

    _logger.info(
        "Iterator: workflow %d/%d complete, pack_generation_complete=%s",
        next_index,
        total,
        complete,
    )

    if complete:
        return f"Pack generation complete ({next_index}/{total} workflows)"
    return f"Workflow {next_index}/{total} complete — continuing to next workflow"
