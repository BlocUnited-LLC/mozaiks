"""Materialize an automatic result tool for a declared event workflow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

from factory_app.workflows._shared.workflow_integration import (
    WorkflowResultWritePlan,
    workflow_name_to_capability_id,
    workflow_result_output_schema,
    workflow_result_write_plans,
)
from factory_app.workflows.AgentGenerator.tools.outcome_materialization import (
    materialize_workflow_outcomes,
)
from mozaiksai.core.workflow.context.authority import TRANSITION_ROUTER_WRITER
from mozaiksai.core.workflow.declarative.contracts import (
    RESULT_TOOL_FILE as _FILE,
)
from mozaiksai.core.workflow.declarative.contracts import (
    RESULT_TOOL_FUNCTION as _TOOL,
)
from mozaiksai.core.workflow.declarative.contracts import (
    ContextVariableDefinitionSpec,
    parse_agents_config,
    parse_context_variables_config,
    parse_tools_config,
)

_ADAPTER = '''from mozaiksai.core.workflow.module_tools import commit_workflow_result


async def save_workflow_result(context_variables):
    """Commit this turn's validated structured result to its triggering record."""
    return await commit_workflow_result(context_variables.get("structured_output"))
'''


def _materialize_event_input(context: dict[str, Any], agent: str) -> None:
    """Only the event router can supply the result agent's triggering payload."""
    parse_context_variables_config(context)
    definitions = context.setdefault("definitions", {})
    definition = definitions.setdefault("event_payload", {"source": {"type": "state", "default": {}}})
    spec = ContextVariableDefinitionSpec.model_validate(definition)
    source = spec.source.model_dump(exclude_defaults=True, exclude_none=True, by_alias=True)
    if (spec.type not in (None, "object", "dict", "map") or spec.source.type != "state"
            or set(source) - {"type", "default", "required", "persist"} or spec.source.default not in (None, {})
            or spec.source.persist is False or spec.persisted is False or spec.authorization
            or spec.authority_class not in (None, "model_visible_information", "mutable_workflow_state")
            or spec.model_visible is False or spec.writer_ids not in ([], [TRANSITION_ROUTER_WRITER])):
        raise ValueError(
            f"{agent} requires event_payload as model-visible object state written only by transition_router. "
            "Remove conflicting source, writer, or visibility declarations."
        )
    definition["type"] = spec.type or "object"
    definition["writer_ids"] = [TRANSITION_ROUTER_WRITER]
    definition["model_visible"] = True
    variables = context.setdefault("agents", {}).setdefault(agent, {}).setdefault("variables", [])
    if "event_payload" not in variables:
        variables.append("event_payload")


def _materialize_event_triggers(
    orchestrator: dict[str, Any], plans: list[WorkflowResultWritePlan], workflow_description: str,
) -> None:
    if orchestrator.get("workflow_startup_mode") != "BackendOnly":
        raise ValueError("Reviewed result writes require a BackendOnly workflow")
    triggers = orchestrator.setdefault("triggers", [])
    if not isinstance(triggers, list) or any(not isinstance(trigger, dict) for trigger in triggers):
        raise ValueError("Reviewed result writes require a list of orchestrator triggers")
    descriptions = {
        plan.event_type: workflow_description or
        f"Compute {', '.join(plan.result_write.fields)} for {plan.event_type} and save through {plan.module_id}.{plan.result_write.action}."
        for plan in plans
    }
    event_triggers = [trigger for trigger in triggers if trigger.get("type") == "event"
                      or (trigger.get("type") is None and "event" in trigger)]
    declared = [trigger.get("event") for trigger in event_triggers]
    # Preserve contradictory declarations for the integration gate to reject.
    if any(event not in descriptions for event in declared):
        return
    capabilities = {trigger["capability_id"] for trigger in event_triggers if trigger.get("capability_id")}
    if len(capabilities) > 1:
        return
    capability = next(iter(capabilities), None) or workflow_name_to_capability_id(orchestrator["workflow_name"])
    for trigger in event_triggers:
        if not trigger.get("capability_id"):
            trigger["capability_id"] = capability
        if not trigger.get("description"):
            trigger["description"] = descriptions[trigger["event"]]
    triggers.extend({"type": "event", "event": event, "description": description, "capability_id": capability}
                    for event, description in descriptions.items() if event not in declared)


def _validate_required_result_path(initial_agent: str, result_agent: str, rules: list[dict[str, Any]]) -> None:
    """A successful terminal before the approved agent would skip the required write."""
    pending = [initial_agent]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        if current == result_agent:
            continue
        for rule in rules:
            if rule["source_agent"] != current:
                continue
            target = rule["target_agent"]
            if target == "terminate":
                if rule.get("termination_reason") != "workflow_failed":
                    raise ValueError("A successful workflow path bypasses the approved result agent")
            else:
                pending.append(target)
    if result_agent not in visited:
        raise ValueError("The approved workflow result agent must be reachable from the initial agent")


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
    workflow_result_output_schema(entry, plans[0])
    orchestrator = yaml.safe_load(files["orchestrator.yaml"]["content"])
    _materialize_event_triggers(orchestrator, plans, str(spec.get("description") or "").strip())
    context = yaml.safe_load(files["context_variables.yaml"]["content"])
    _materialize_event_input(context, agent)
    tools = yaml.safe_load(files["tools.yaml"]["content"])
    parse_tools_config(tools)
    binding = {
        "agent": agent, "file": _FILE, "function": _TOOL, "description": "Commit the approved workflow result.",
        "tool_type": "Agent_Tool", "auto_tool_call": True, "bind_to_agent": False,
    }
    bindings = tools.setdefault("tools", [])
    existing = [item for item in bindings if isinstance(item, Mapping)
                and (item.get("function") == _TOOL or item.get("file") == _FILE)]
    if existing and [{key: value for key, value in item.items() if key != "outcome"} for item in existing] != [binding]:
        raise ValueError("The workflow result tool binding is owned by the Factory")
    if not existing:
        bindings.append(binding)
    parse_tools_config(tools)
    if _FILE in files and files[_FILE].get("content") != _ADAPTER:
        raise ValueError("The workflow result adapter is owned by the Factory")
    manifests = {"tools.yaml": tools, "context_variables.yaml": context, "orchestrator.yaml": orchestrator}
    materialized = [
        {**file, "content": yaml.safe_dump(manifests[file["filename"]], sort_keys=False, allow_unicode=True)}
        if file["filename"] in manifests else file for file in entry["files"]
    ]
    if _FILE not in files:
        materialized.append({"filename": _FILE, "content": _ADAPTER})
    compiled = materialize_workflow_outcomes({
        **entry, "files": materialized, "result_write_plans": [plan.model_dump(mode="json") for plan in plans],
    }, required_save=(agent, _TOOL))
    initial = orchestrator["initial_agent"]
    graph = next(file for file in compiled["files"] if file["filename"] == "transition_graph.yaml")
    _validate_required_result_path(initial, agent, yaml.safe_load(graph["content"])["transition_rules"])
    return compiled
