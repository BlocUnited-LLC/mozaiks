"""Transition graph validation for the AG2 beta Network workflow model."""

from __future__ import annotations

from typing import Any

from logs.logging_config import get_workflow_logger

from ..execution.network_graph import (
    WorkflowGraphCompileError,
    compile_transition_rules_to_graph,
)
from ..workflow_manager import workflow_manager

log = get_workflow_logger("transition_graph")


def wire_transition_graph_with_debugging(workflow_name: str, agents: dict[str, Any]) -> dict[str, Any]:
    """Validate transition rules and return a summary dict."""
    return _validate_transition_rules(workflow_name, agents)


def _validate_transition_rules(workflow_name: str, agents: dict[str, Any]) -> dict[str, Any]:
    """Scan transition_graph.yaml for this workflow and verify all referenced agents exist."""
    config = workflow_manager.get_config(workflow_name) or {}
    transition_block = config.get("transition_graph", {})
    rules = transition_block.get("transition_rules", []) or []

    summary: dict[str, Any] = {
        "workflow": workflow_name,
        "rules_total": len(rules),
        "agents_with_rules": set(),
        "missing_source_agents": [],
        "missing_target_agents": [],
        "errors": [],
    }

    _special_sources = {"user", "User", "user_proxy", "userproxy", "userproxyagent"}
    _special_targets = _special_sources | {"terminate", "Terminate", "TERMINATE"}

    for rule in rules:
        src = rule.get("source_agent")
        tgt = rule.get("target_agent")

        if not src or not tgt:
            summary["errors"].append(f"Rule missing source/target: {rule}")
            continue

        if src not in _special_sources and src not in agents:
            summary["missing_source_agents"].append(src)
            log.warning("[TRANSITION_GRAPH] Source agent '%s' not present in workflow '%s'", src, workflow_name)

        if tgt not in _special_targets and tgt not in agents:
            summary["missing_target_agents"].append(tgt)
            log.warning("[TRANSITION_GRAPH] Target agent '%s' not present in workflow '%s'", tgt, workflow_name)

        summary["agents_with_rules"].add(src)

    if not summary["errors"]:
        try:
            initial_agent = _initial_agent_for_workflow(workflow_name, agents)
            compile_transition_rules_to_graph(
                rules,
                initial_agent_name=initial_agent,
                agent_id_by_name={name: name for name in agents},
            )
        except WorkflowGraphCompileError as exc:
            summary["errors"].append(str(exc))

    summary["agents_with_rules"] = list(summary["agents_with_rules"])
    return summary


def _initial_agent_for_workflow(workflow_name: str, agents: dict[str, Any]) -> str:
    config = workflow_manager.get_config(workflow_name) or {}
    initial = str(config.get("initial_agent") or "").strip()
    if initial:
        return initial
    return next(iter(agents), "user")


__all__ = [
    "wire_transition_graph_with_debugging",
]
