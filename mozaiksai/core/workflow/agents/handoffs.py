"""Handoff validation for the AG2 beta Network workflow model."""

from __future__ import annotations

from typing import Any, Dict

from ..execution.network_graph import (
    WorkflowGraphCompileError,
    compile_handoffs_to_transition_graph,
)
from ..workflow_manager import workflow_manager
from logs.logging_config import get_workflow_logger

log = get_workflow_logger("handoffs")


def wire_handoffs(workflow_name: str, agents: Dict[str, Any]) -> None:
    """Pre-validate handoff rules for the workflow.

    Routing is compiled to an AG2 beta Network TransitionGraph. This function
    runs at agent-creation time to surface misconfigured rules early.
    """
    summary = _validate_handoff_rules(workflow_name, agents)
    log.info(
        "HANDOFFS_VALIDATED workflow=%s rules=%d agents_covered=%d errors=%d",
        workflow_name,
        summary["rules_total"],
        len(summary["agents_with_rules"]),
        len(summary["errors"]),
    )
    if summary["errors"]:
        for err in summary["errors"]:
            log.warning("[HANDOFFS] Validation issue: %s", err)


def wire_handoffs_with_debugging(workflow_name: str, agents: Dict[str, Any]) -> Dict[str, Any]:
    """Validate handoff rules and return a summary dict (kept for API compat)."""
    return _validate_handoff_rules(workflow_name, agents)


def _validate_handoff_rules(workflow_name: str, agents: Dict[str, Any]) -> Dict[str, Any]:
    """Scan handoffs.yaml for this workflow and verify all referenced agents exist."""
    config = workflow_manager.get_config(workflow_name) or {}
    handoffs_block = config.get("handoffs", {})
    rules = handoffs_block.get("handoff_rules", []) or []

    summary: Dict[str, Any] = {
        "workflow": workflow_name,
        "rules_total": len(rules),
        "agents_with_rules": set(),
        "missing_source_agents": [],
        "missing_target_agents": [],
        "errors": [],
    }

    _special = {"user", "terminate", "User", "Terminate", "TERMINATE", "END", "end", "stop"}

    for rule in rules:
        src = rule.get("source_agent")
        tgt = rule.get("target_agent")

        if not src or not tgt:
            summary["errors"].append(f"Rule missing source/target: {rule}")
            continue

        if src not in agents:
            summary["missing_source_agents"].append(src)
            log.warning("[HANDOFFS] Source agent '%s' not present in workflow '%s'", src, workflow_name)

        if tgt not in _special and tgt not in agents:
            summary["missing_target_agents"].append(tgt)
            log.warning("[HANDOFFS] Target agent '%s' not present in workflow '%s'", tgt, workflow_name)

        summary["agents_with_rules"].add(src)

    if not summary["errors"]:
        try:
            initial_agent = _initial_agent_for_workflow(workflow_name, agents)
            compile_handoffs_to_transition_graph(
                rules,
                initial_agent_name=initial_agent,
                agent_id_by_name={name: name for name in agents},
            )
        except WorkflowGraphCompileError as exc:
            summary["errors"].append(str(exc))

    summary["agents_with_rules"] = list(summary["agents_with_rules"])
    return summary


def _initial_agent_for_workflow(workflow_name: str, agents: Dict[str, Any]) -> str:
    config = workflow_manager.get_config(workflow_name) or {}
    initial = str(config.get("initial_agent") or "").strip()
    if initial:
        return initial
    return next(iter(agents), "user")


__all__ = [
    "wire_handoffs",
    "wire_handoffs_with_debugging",
]
