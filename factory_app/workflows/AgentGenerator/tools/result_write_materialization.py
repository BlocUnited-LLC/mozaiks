"""Materialize an automatic result tool for a declared event workflow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

from factory_app.workflows._shared.workflow_integration import workflow_result_write_plans
from mozaiksai.core.workflow.declarative.contracts import (
    parse_agents_config,
    parse_structured_outputs_config,
    parse_tools_config,
)

_TOOL = "save_workflow_result"
_FILE = f"tools/{_TOOL}.py"
_ADAPTER = '''from mozaiksai.core.workflow.module_tools import commit_workflow_result


async def save_workflow_result(context_variables):
    """Commit this turn's validated structured result to its triggering record."""
    return await commit_workflow_result(context_variables.get("structured_output"))
'''


def materialize_workflow_result_write(entry: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    plans = workflow_result_write_plans(spec)
    if entry.get("result_write_plans") and workflow_result_write_plans(entry) != plans:
        raise ValueError("The workflow builder cannot change result_write_plans from workflow review")
    if not plans:
        return entry
    agent = plans[0].agent
    fields = set(plans[0].result_write.fields)
    if any(plan.agent != agent or set(plan.result_write.fields) != fields for plan in plans):
        raise ValueError("One workflow must use the same result agent and output fields for every result-write trigger")

    files = {file["filename"]: file for file in entry["files"]}
    agents = parse_agents_config(yaml.safe_load(files["agents.yaml"]["content"]))["agents"]
    if not agents.get(agent, {}).get("structured_outputs_required"):
        raise ValueError(f"{agent} must be a local agent with structured_outputs_required: true")
    outputs = parse_structured_outputs_config(yaml.safe_load(files["structured_outputs.yaml"]["content"]))
    model = outputs.get("registry", {}).get(agent)
    output_fields = outputs.get("models", {}).get(model, {}).get("fields", {})
    if set(output_fields) != fields or any("default" in field for field in output_fields.values()):
        raise ValueError("The result agent's structured output must declare exactly the approved result fields")
    tools = yaml.safe_load(files["tools.yaml"]["content"])
    parse_tools_config(tools)
    binding = {
        "agent": agent, "file": _FILE, "function": _TOOL, "description": "Commit the approved workflow result.",
        "tool_type": "Agent_Tool", "auto_tool_call": True, "bind_to_agent": False,
    }
    bindings = tools.setdefault("tools", [])
    existing = [item for item in bindings if isinstance(item, Mapping)
                and (item.get("function") == _TOOL or item.get("file") == _FILE)]
    if existing and existing != [binding]:
        raise ValueError("The workflow result tool binding is owned by the Factory")
    if not existing:
        bindings.append(binding)
    parse_tools_config(tools)
    if _FILE in files and files[_FILE].get("content") != _ADAPTER:
        raise ValueError("The workflow result adapter is owned by the Factory")
    content = yaml.safe_dump(tools, sort_keys=False, allow_unicode=True)
    materialized = [{**file, "content": content} if file["filename"] == "tools.yaml" else file for file in entry["files"]]
    if _FILE not in files:
        materialized.append({"filename": _FILE, "content": _ADAPTER})
    return {**entry, "files": materialized, "result_write_plans": [plan.model_dump(mode="json") for plan in plans]}
