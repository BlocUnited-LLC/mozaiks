"""Materialize typed operation plans into the existing workflow YAML contracts."""

from __future__ import annotations

from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.workflow.contract_validation import validate_workflow_tool_outcomes
from mozaiksai.core.workflow.declarative.contracts import (
    ToolOutcomeSpec,
    parse_context_variables_config,
    parse_tools_config,
    parse_transition_graph_config,
)


class OutcomeRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    target_agent: str
    termination_reason: Literal["workflow_complete", "workflow_failed"] | None = None


class WorkflowOutcomePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    function: str
    context_key: str
    attempts_key: str
    result_field: str = "status"
    error_value: str
    max_attempts: int = Field(default=1, strict=True, ge=1, le=100)
    retry_on: list[str] = Field(default_factory=list)
    routes: list[OutcomeRoute] = Field(min_length=1)

    def tool_outcome(self) -> ToolOutcomeSpec:
        return ToolOutcomeSpec.model_validate({
            **self.model_dump(exclude={"agent", "function", "routes"}),
            "values": [route.value for route in self.routes],
        })


def materialize_workflow_outcomes(entry: dict[str, Any]) -> dict[str, Any]:
    raw_plans = entry.get("outcome_plans", [])
    if not isinstance(raw_plans, list):
        raise ValueError("outcome_plans must be a list")
    if not raw_plans:
        return entry
    plans = [WorkflowOutcomePlan.model_validate(plan) for plan in raw_plans]
    if len({plan.agent for plan in plans}) != len(plans):
        raise ValueError("outcome_plans must have one operation per agent")
    files = {file["filename"]: file for file in entry["files"]}
    if len(files) != len(entry["files"]):
        raise ValueError("workflow files contain duplicate filenames")
    payloads: dict[str, dict] = {}
    for filename in ("tools.yaml", "context_variables.yaml", "transition_graph.yaml", "structured_outputs.yaml"):
        if filename not in files:
            raise ValueError(f"outcome materialization requires {filename}")
        payload = yaml.safe_load(files[filename]["content"])
        if not isinstance(payload, dict):
            raise ValueError(f"{filename} must contain a mapping")
        payloads[filename.removesuffix(".yaml")] = payload
    tools = payloads["tools"].get("tools", [])
    definitions = payloads["context_variables"].setdefault("definitions", {})
    rules = payloads["transition_graph"].setdefault("transition_rules", [])
    owned_keys: set[str] = set()
    for plan in plans:
        outcome = plan.tool_outcome()
        matches = [tool for tool in tools if tool.get("agent") == plan.agent and tool.get("function") == plan.function]
        if len(matches) != 1:
            raise ValueError(f"outcome plan must resolve one tool: {plan.agent}.{plan.function}")
        matches[0].update(auto_tool_call=True, bind_to_agent=False, outcome=outcome.model_dump())
        for key, kind, default, authority in (
            (plan.context_key, "string", plan.error_value, "closed_writer_routing_state"),
            (plan.attempts_key, "integer", 0, "closed_writer_quality_state"),
        ):
            if key in owned_keys:
                raise ValueError(f"duplicate operation state key: {key}")
            owned_keys.add(key)
            definition = {
                "type": kind,
                "authority_class": authority,
                "writer_ids": ["deterministic_tool"],
                "persisted": True,
                "source": {"type": "state", "default": default},
            }
            if key in definitions and definitions[key] != definition:
                raise ValueError(f"outcome plan conflicts with an existing context definition: {key}")
            definitions[key] = definition
        planned_rules = [{
            "source_agent": plan.agent,
            "target_agent": route.target_agent,
            "transition_type": "condition",
            "condition_type": "context_equals",
            "condition_key": plan.context_key,
            "condition_value": route.value,
            **({"termination_reason": route.termination_reason} if route.termination_reason else {}),
        } for route in plan.routes]
        error_route = next(route for route in plan.routes if route.value == plan.error_value)
        planned_rules.append({
            "source_agent": plan.agent, "target_agent": error_route.target_agent, "transition_type": "after_turn",
            **({"termination_reason": error_route.termination_reason} if error_route.termination_reason else {}),
        })
        existing_rules = [rule for rule in rules if rule.get("source_agent") == plan.agent]
        if existing_rules and existing_rules != planned_rules:
            raise ValueError(f"outcome plan conflicts with existing transitions: {plan.agent}")
        rules[:] = [rule for rule in rules if rule.get("source_agent") != plan.agent]
        rules.extend(planned_rules)

    parse_tools_config(payloads["tools"])
    parse_context_variables_config(payloads["context_variables"])
    parse_transition_graph_config(payloads["transition_graph"])
    validate_workflow_tool_outcomes({**payloads, "tools": tools})
    rewritten = {
        f"{name}.yaml": yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        for name, payload in payloads.items() if name != "structured_outputs"
    }
    return {
        **entry,
        "files": [{**file, "content": rewritten.get(file["filename"], file["content"])} for file in entry["files"]],
    }
