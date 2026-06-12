"""
Generate module_interface.yaml files from agent_backend_integration build tasks.

Each agent_backend_integration task carries structured context_variables:
  - workflow_name   (str)   — workflow directory name
  - module_actions  (str)   — JSON array of {module_id, action_id, description}
  - emits_events    (str)   — JSON array of {event_type, target_module_id}

This tool reads those entries from the build plan and emits a canonical
module_interface.yaml file per workflow. No reasoning is done here — AppPlanAgent
already produced the structured data; this tool just serializes it to YAML.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "mozaiks.module_interface.v1"


def _extract_cv(context_variables: Any, key: str) -> Optional[str]:
    """Extract a string value from a context_variables list or dict."""
    if isinstance(context_variables, dict):
        value = context_variables.get(key)
        return str(value) if value is not None else None
    if isinstance(context_variables, list):
        for entry in context_variables:
            if isinstance(entry, dict) and str(entry.get("key") or "").strip() == key:
                value = entry.get("value")
                return str(value) if value is not None else None
    return None


def _parse_json_list(raw: Optional[str], field: str, task_id: str) -> List[Dict[str, Any]]:
    if not raw or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        logger.warning("task %s: %s is not a JSON array — skipping", task_id, field)
        return []
    except json.JSONDecodeError as exc:
        logger.warning("task %s: failed to parse %s JSON: %s", task_id, field, exc)
        return []


def generate_module_interface_files(
    build_plan: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """
    Generate module_interface.yaml code_file entries from agent_backend_integration tasks.

    Args:
        build_plan: The normalized app_build_plan dict from context_variables.

    Returns:
        List of {filename, content} dicts ready to merge into code_files.
    """
    if not isinstance(build_plan, dict):
        return []

    build_tasks = build_plan.get("build_tasks") or []
    if not isinstance(build_tasks, list):
        return []

    files: List[Dict[str, str]] = []

    for task in build_tasks:
        if not isinstance(task, dict):
            continue
        task_type = str(task.get("task_type") or "").strip()
        if task_type != "agent_backend_integration":
            continue

        task_id = str(task.get("task_id") or "<unknown>")
        cv = task.get("context_variables")

        workflow_name = _extract_cv(cv, "workflow_name")
        if not workflow_name or not workflow_name.strip():
            logger.warning(
                "task %s: agent_backend_integration missing workflow_name context variable — skipping",
                task_id,
            )
            continue

        module_actions = _parse_json_list(_extract_cv(cv, "module_actions"), "module_actions", task_id)
        emits_events = _parse_json_list(_extract_cv(cv, "emits_events"), "emits_events", task_id)

        if not module_actions:
            logger.warning(
                "task %s (workflow=%s): module_actions is empty — module_interface.yaml will be minimal",
                task_id,
                workflow_name,
            )

        manifest: Dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "module_actions": [
                {
                    "module_id": str(action.get("module_id") or ""),
                    "action_id": str(action.get("action_id") or ""),
                    "description": str(action.get("description") or ""),
                }
                for action in module_actions
            ],
        }
        if emits_events:
            manifest["emits_events"] = [
                {
                    "event_type": str(ev.get("event_type") or ""),
                    "target_module_id": str(ev.get("target_module_id") or ""),
                }
                for ev in emits_events
            ]

        filename = f"workflows/{workflow_name}/module_interface.yaml"
        content = yaml.dump(manifest, default_flow_style=False, sort_keys=False, allow_unicode=True)
        files.append({"filename": filename, "content": content})
        logger.info("Generated %s (%d actions)", filename, len(module_actions))

    return files


__all__ = ["generate_module_interface_files"]

