"""
workflow_strategy tool - caches module-based workflow architecture from WorkflowStrategyAgent.

This tool persists the high-level strategy (workflow metadata + modules) so downstream tools
can merge ModuleAgents into an Action Plan without re-deriving strategy fields.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, List, Optional

_logger = logging.getLogger("tools.workflow_strategy")


def _normalize_pattern(pattern_value: Any) -> List[str]:
    """Ensure pattern is stored as a non-empty list of strings."""
    if isinstance(pattern_value, list):
        return [str(item).strip() for item in pattern_value if str(item).strip()]
    if isinstance(pattern_value, str):
        cleaned = pattern_value.strip()
        return [cleaned] if cleaned else []
    return []


def _normalize_modules(value: Any) -> List[Dict[str, Any]]:
    """Coerce modules/phases payload into a normalized modules list."""
    modules: List[Dict[str, Any]] = []
    if not isinstance(value, list):
        return modules

    for idx, entry in enumerate(value):
        if not isinstance(entry, dict):
            continue
        module = dict(entry)
        try:
            module_index = int(module.get("module_index", idx))
        except (TypeError, ValueError):
            module_index = idx
        module["module_index"] = module_index
        modules.append(module)
    return modules


def workflow_strategy(
    *,
    WorkflowStrategy: Annotated[
        Optional[Dict[str, Any]],
        "High-level workflow architecture payload produced by WorkflowStrategyAgent",
    ],
    agent_message: Annotated[Optional[str], "Optional confirmation message echoed to the chat"] = None,
    context_variables: Annotated[Optional[Any], "Context variables provided by AG2"] = None,
) -> str:
    """
    Cache workflow strategy from WorkflowStrategyAgent.

    Stores the module-based workflow architecture in context variables, triggering
    handoff to WorkflowImplementationAgent for detailed agent/tool design.
    """

    if not WorkflowStrategy or not isinstance(WorkflowStrategy, dict):
        raise ValueError("WorkflowStrategy payload is required and must be a dictionary")

    workflow_name = str(WorkflowStrategy.get("workflow_name") or "").strip()
    workflow_description = str(WorkflowStrategy.get("workflow_description") or "").strip()
    trigger = str(WorkflowStrategy.get("trigger") or WorkflowStrategy.get("trigger_type") or "").strip()
    initiated_by = str(WorkflowStrategy.get("initiated_by") or WorkflowStrategy.get("initiatedBy") or "user").strip() or "user"
    human_in_loop = bool(WorkflowStrategy.get("human_in_loop", False))
    pattern = _normalize_pattern(WorkflowStrategy.get("pattern"))
    modules = _normalize_modules(WorkflowStrategy.get("modules") or WorkflowStrategy.get("phases"))
    lifecycle_operations = WorkflowStrategy.get("lifecycle_operations") or []
    strategy_notes = str(WorkflowStrategy.get("strategy_notes") or "").strip()

    if not workflow_name:
        raise ValueError("WorkflowStrategy.workflow_name is required")
    if not workflow_description:
        raise ValueError("WorkflowStrategy.workflow_description is required")
    if not trigger:
        raise ValueError("WorkflowStrategy.trigger is required")
    if not pattern:
        raise ValueError("WorkflowStrategy.pattern is required")

    strategy = {
        "workflow_name": workflow_name,
        "workflow_description": workflow_description,
        "trigger": trigger,
        "trigger_type": trigger,
        "initiated_by": initiated_by,
        "human_in_loop": human_in_loop,
        "pattern": pattern,
        "modules": modules,
        "lifecycle_operations": lifecycle_operations,
        "strategy_notes": strategy_notes,
    }

    if context_variables and hasattr(context_variables, "set"):
        try:
            context_variables.set("workflow_strategy", strategy)  # type: ignore[attr-defined]
            context_variables.set("strategy_ready", True)  # type: ignore[attr-defined]
            _logger.info(
                "Cached workflow strategy for '%s' with pattern '%s' and %d modules",
                workflow_name,
                ", ".join(pattern),
                len(modules),
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            _logger.error("Failed to cache workflow strategy: %s", exc)
            return f"Error caching strategy: {exc}"
    else:
        _logger.warning("context_variables not available or missing 'set' method")

    confirmation_message = agent_message.strip() if isinstance(agent_message, str) else ""
    if not confirmation_message:
        confirmation_message = "Workflow strategy cached successfully."

    return (
        f"{confirmation_message}\n\n"
        f"Workflow: {workflow_name}\n"
        f"Pattern: {', '.join(pattern)}\n"
        f"Trigger: {trigger} (initiated_by={initiated_by})\n"
        f"Modules: {len(modules)}\n"
        f"Lifecycle Ops: {len(lifecycle_operations)}\n\n"
        "Handing off to WorkflowImplementationAgent for detailed implementation..."
    )
