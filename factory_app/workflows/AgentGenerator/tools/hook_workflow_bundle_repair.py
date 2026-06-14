"""Prompt hook for AgentGenerator workflow-bundle repair merges."""

from __future__ import annotations

from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section
from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
    merge_workflow_bundle_repair_results,
)

_HEADER = "[WORKFLOW BUNDLE REPAIR]"


def merge_repaired_workflow_bundle_results(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Merge repaired workflow task outputs before PackMetadataAgent reads context."""

    if getattr(agent, "name", "") != "PackMetadataAgent":
        return
    context_variables = getattr(agent, "context_variables", None)
    result = merge_workflow_bundle_repair_results(context_variables)
    if result.get("status") != "merged":
        return
    body = (
        "Workflow bundle repair outputs were merged into the full bundle result set.\n"
        f"- repaired_workflows: {result.get('repaired_workflows') or []}\n"
        f"- merged_workflow_count: {result.get('merged_workflow_count')}\n"
        "Use workflow_bundle_results from context as the authoritative merged pack."
    )
    update_agent_section(agent, _HEADER, body)


__all__ = ["merge_repaired_workflow_bundle_results"]
