"""Cross-file validation for workflow declarative contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .task_batches import TaskBatchesConfig

_CONTEXT_VARIABLE_REF = re.compile(r"\$\{([^}]+)\}")


def validate_workflow_context_contract(
    *,
    workflow_name: str,
    workflow_config: dict[str, Any],
    workflow_path: Path | None = None,
) -> None:
    """Validate context references, including on-disk task batches when supplied."""

    context_config = workflow_config.get("context_variables")
    declared = set((context_config or {}).get("definitions") or {})
    missing: list[str] = []

    for agent_name, view in ((context_config or {}).get("agents") or {}).items():
        if not isinstance(view, dict):
            continue
        exposed = view.get("variables") or []
        for variable in sorted(str(item).strip() for item in exposed if str(item or "").strip()):
            if variable not in declared:
                missing.append(
                    f"context_variables agents.{agent_name}.variables references undeclared context variable {variable!r}"
                )

    transition_rules = (workflow_config.get("transition_graph") or {}).get("transition_rules") or []
    for rule in transition_rules:
        if not isinstance(rule, dict):
            continue
        condition_type = str(rule.get("condition_type") or "").strip()
        if condition_type not in {"context_equals", "context_expression"}:
            continue
        source = str(rule.get("source_agent") or "").strip() or "unknown"
        target = str(rule.get("target_agent") or "").strip() or "unknown"
        if condition_type == "context_equals":
            variable = str(rule.get("condition_key") or "").strip()
            if variable and variable not in declared:
                missing.append(
                    f"transition_graph {source}->{target} references undeclared context variable {variable!r}"
                )
            continue
        expression = str(rule.get("context_expression") or "").strip()
        for variable in sorted(_context_expression_refs(expression)):
            if variable not in declared:
                missing.append(
                    f"transition_graph {source}->{target} references undeclared context variable {variable!r}"
                )

    task_batches = None
    task_batches_path = workflow_path / "extended_orchestration" / "task_batches.yaml" if workflow_path else None
    if task_batches_path is not None and task_batches_path.exists():
        from mozaiksai.core.workflow.task_batches import parse_task_batches_config

        raw = yaml.safe_load(task_batches_path.read_text(encoding="utf-8")) or {}
        task_batches = parse_task_batches_config(raw)
        for batch in task_batches.batches:
            for key in (batch.result.context_key, batch.result.status_key):
                if key not in declared:
                    missing.append(
                        f"task_batches {batch.id!r} writes undeclared context variable {key!r}"
                    )

    if missing:
        joined = "; ".join(missing)
        raise ValueError(f"{workflow_name} context_variables.yaml contract mismatch: {joined}")

    validate_workflow_tool_outcomes(workflow_config, task_batches=task_batches)


def validate_workflow_tool_outcomes(
    workflow_config: dict[str, Any], *, task_batches: TaskBatchesConfig | None = None,
) -> None:
    """Check outcome producers, protected state, and complete source-scoped routes."""
    from .declarative.contracts import ToolSpec

    raw_tools = workflow_config.get("tools") or []
    definitions = (workflow_config.get("context_variables") or {}).get("definitions") or {}
    rules = (workflow_config.get("transition_graph") or {}).get("transition_rules") or []
    registry = (workflow_config.get("structured_outputs") or {}).get("registry") or {}
    task_agents: set[str] = set()
    if task_batches is not None:
        task_agents.add(str(workflow_config.get("initial_agent") or ""))
        for batch in task_batches.batches:
            task_agents.add(batch.trigger_agent)
            task_agents.update(batch.allowed_execution_agents)
    errors: list[str] = []
    owned_keys: set[str] = set()
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, dict) or raw_tool.get("outcome") is None:
            continue
        tool = ToolSpec.model_validate(raw_tool)
        outcome = tool.outcome
        assert outcome is not None
        owners = [tool.agent] if isinstance(tool.agent, str) else tool.agent
        if len(owners) != 1:
            errors.append(f"{tool.function}: an outcome operation must have exactly one agent")
            continue
        owner = owners[0]
        if owner in task_agents:
            errors.append(f"{owner}: graph outcome operations cannot be task-batch triggers or workers; use a separate network agent")
        if not registry.get(owner):
            errors.append(f"{owner}: outcome producer requires a registered structured output model")
        auto_tools = [
            candidate for candidate in raw_tools
            if candidate.get("auto_tool_call") is True
            and owner in ([candidate.get("agent")] if isinstance(candidate.get("agent"), str) else candidate.get("agent", []))
        ]
        if len(auto_tools) != 1:
            errors.append(f"{owner}: outcome producer must be the agent's only auto-invoked tool")
        for key, expected_type, default, authority in (
            (outcome.context_key, "string", outcome.error_value, "closed_writer_routing_state"),
            (outcome.attempts_key, "integer", 0, "closed_writer_quality_state"),
        ):
            if key in owned_keys:
                errors.append(f"outcome state {key!r} must belong to one operation")
            owned_keys.add(key)
            definition = definitions.get(key) or {}
            source = definition.get("source") or {}
            if (
                definition.get("type") != expected_type
                or definition.get("authority_class") != authority
                or definition.get("writer_ids") != ["deterministic_tool"]
                or definition.get("persisted") is not True
                or source.get("type") != "state"
                or source.get("triggers")
                or type(source.get("default")) is not type(default)
                or source.get("default") != default
            ):
                errors.append(f"{key!r}: outcome state requires a persisted {expected_type} state with default {default!r}, {authority}, and only deterministic_tool writes")
        source_rules = [rule for rule in rules if rule.get("source_agent") == owner]
        seen: set[str] = set()
        fallbacks = 0
        for rule in source_rules:
            if rule.get("transition_type") == "after_turn":
                fallbacks += 1
                if not _failure_destination(rule):
                    errors.append(f"{owner}: outcome fallback must route to user or terminate as workflow_failed")
                continue
            value = rule.get("condition_value")
            if (
                rule.get("condition_type") != "context_equals"
                or rule.get("condition_key") != outcome.context_key
                or not isinstance(value, str)
                or value not in outcome.values
            ):
                errors.append(f"{owner}: routes must test declared outcomes on {outcome.context_key!r}")
                continue
            if value in seen:
                errors.append(f"{owner}: ambiguous route for outcome {value!r}")
            seen.add(value)
            if value == outcome.error_value and not _failure_destination(rule):
                errors.append(f"{owner}: error_value must route to user or terminate as workflow_failed")
        if fallbacks > 1:
            errors.append(f"{owner}: duplicate outcome fallback")
        for value in sorted(set(outcome.values) - seen):
            errors.append(f"{owner}: missing route for outcome {value!r}")
    if errors:
        raise ValueError("tools.yaml outcome contract mismatch: " + "; ".join(errors))


def _failure_destination(rule: dict[str, Any]) -> bool:
    return rule.get("target_agent") == "user" or (
        rule.get("target_agent") == "terminate" and rule.get("termination_reason") == "workflow_failed"
    )


def _context_expression_refs(expression: str) -> set[str]:
    return {
        match.group(1).strip()
        for match in _CONTEXT_VARIABLE_REF.finditer(expression)
        if match.group(1).strip()
    }


__all__ = ["validate_workflow_context_contract", "validate_workflow_tool_outcomes"]
