"""Cross-file validation for workflow declarative contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_CONTEXT_VARIABLE_REF = re.compile(r"\$\{([^}]+)\}")


def validate_workflow_context_contract(
    *,
    workflow_name: str,
    workflow_config: dict[str, Any],
    workflow_path: Path,
) -> None:
    """Validate context declarations against routing and task-batch contracts."""

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

    task_batches_path = workflow_path / "extended_orchestration" / "task_batches.yaml"
    if task_batches_path.exists():
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


def _context_expression_refs(expression: str) -> set[str]:
    return {
        match.group(1).strip()
        for match in _CONTEXT_VARIABLE_REF.finditer(expression)
        if match.group(1).strip()
    }


__all__ = ["validate_workflow_context_contract"]
