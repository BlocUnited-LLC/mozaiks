"""Audit persisted, validated ServiceAgent files before AG2 quality routing."""

from __future__ import annotations

from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section
from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
    review_module_runtime_quality,
)

_HEADER = "[MODULE RUNTIME QUALITY GATE]"


def run_module_runtime_quality_gate(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Inspect persisted code, never extract authoritative files from chat prose."""
    if getattr(agent, "name", "") != "ModuleRuntimeQualityAgent":
        return
    result = review_module_runtime_quality(context_variables=getattr(agent, "context_variables", None))
    warnings = result.get("warnings") or []
    body = (
        "Deterministic quality gate already ran.\n"
        f"- module_runtime_quality_status: {result['status']}\n"
        f"- module_backend_files_audited: {result['module_backend_file_count']}\n"
        f"- warning_count: {len(warnings)}\n"
        "Emit the ModuleRuntimeQualityReviewRequest JSON only; do not re-audit manually."
    )
    if warnings:
        body += "\n\nWarnings:\n" + "\n".join(f"  - {warning}" for warning in warnings)
    update_agent_section(agent, _HEADER, body)


