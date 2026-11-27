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
    retry_without_attempt: list[str] = Field(default_factory=list)
    routes: list[OutcomeRoute] = Field(min_length=1)

    def tool_outcome(self) -> ToolOutcomeSpec:
        return ToolOutcomeSpec.model_validate({
            **self.model_dump(exclude={"agent", "function", "routes"}),
            "values": [route.value for route in self.routes],
        })


def _required_save_plan(agent: str, function: str, rules: list[dict[str, Any]]) -> WorkflowOutcomePlan:
    """Use the reviewed agent's normal continuation only after a confirmed record write."""
    state_key = "workflow_result_write_status"
    owned = [rule for rule in rules if rule.get("source_agent") == agent]
    target = "terminate"
    if len(owned) == 1 and owned[0].get("transition_type") == "after_turn":
        rule = owned[0]
        if rule.get("termination_reason") == "workflow_failed":
            raise ValueError("The workflow result agent needs a successful continuation")
        target = rule["target_agent"]
        rules.remove(rule)
    elif owned:
        # An already materialized bundle must agree with every canonical route below.
        success = next((rule for rule in owned if rule.get("condition_key") == state_key
                        and rule.get("condition_value") == "applied"), None)
        if success is None:
            raise ValueError("The workflow result agent needs one unconditional success continuation")
        target = success["target_agent"]
    return WorkflowOutcomePlan(
        agent=agent, function=function, context_key=state_key, attempts_key="workflow_result_write_attempts",
        # A lost turn acknowledgement may replay an already committed save.
        # The bound storage receipt adjudicates replays without consuming an attempt.
        max_attempts=2, retry_on=["applied", "already_applied"], retry_without_attempt=["applied", "already_applied"],
        error_value="failed", routes=[
            OutcomeRoute(value="applied", target_agent=target),
            OutcomeRoute(value="already_applied", target_agent=target),
            OutcomeRoute(value="failed", target_agent="terminate", termination_reason="workflow_failed"),
        ],
    )


def materialize_workflow_outcomes(
    entry: dict[str, Any], *, required_save: tuple[str, str] | None = None,
) -> dict[str, Any]:
    raw_plans = entry.get("outcome_plans", [])
    if not isinstance(raw_plans, list):
        raise ValueError("outcome_plans must be a list")
    if not raw_plans and required_save is None:
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
    if required_save is not None:
        agent, function = required_save
        if any(plan.agent == agent for plan in plans):
            raise ValueError(
                f"Factory supplies the save operation for {agent}. Remove that agent's outcome_plan, "
                "its generated save tool binding and its Python implementation. Keep the agent's "
                "structured output and one after_turn success continuation."
            )
        plans.append(_required_save_plan(agent, function, rules))
    owned_keys: set[str] = set()
    for plan in plans:
        outcome = plan.tool_outcome()
        matches = [tool for tool in tools if tool.get("agent") == plan.agent and tool.get("function") == plan.function]
        if len(matches) != 1:
            raise ValueError(f"outcome plan must resolve one tool: {plan.agent}.{plan.function}")
        if required_save == (plan.agent, plan.function) and matches[0].get("outcome") not in (None, outcome.model_dump()):
            raise ValueError("The workflow result tool outcome is owned by the Factory")
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
