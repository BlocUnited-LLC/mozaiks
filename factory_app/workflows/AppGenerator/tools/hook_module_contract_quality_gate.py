"""
Hook: Module Contract Quality Gate

Fires as an prompt middleware function on ModuleContractQualityAgent.

Runs the deterministic module contract audit before the agent speaks so that
AG2 handoff conditions (``module_contract_quality_status == "passed"`` /
``"blocked"``) are already set when the agent reply is evaluated.

Pattern mirrors hook_app_ui_quality_gate.py.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from factory_app.workflows._shared.hook_utils import update_agent_section
from factory_app.workflows.AppGenerator.tools.review_module_contract_quality import (
    review_module_contract_quality,
)

logger = logging.getLogger(__name__)

_HEADER = "[MODULE CONTRACT QUALITY GATE]"


def run_module_contract_quality_gate(
    agent: Any,
    messages: List[Dict[str, Any]],
) -> None:
    """Run the module contract quality gate before ModuleContractQualityAgent replies.

    AG2 evaluates handoff conditions immediately after the agent reply.
    Running the audit here (pre-reply) ensures the context variables the
    handoff rules depend on are already set.
    """
    if getattr(agent, "name", "") != "ModuleContractQualityAgent":
        return

    context_variables = getattr(agent, "context_variables", None)

    result = review_module_contract_quality(context_variables=context_variables)
    status = result.get("status")
    warnings = result.get("warnings") or []
    count = result.get("module_contract_count", 0)

    body = (
        f"Deterministic quality gate already ran.\n"
        f"- module_contract_quality_status: {status}\n"
        f"- module_contracts_audited: {count}\n"
        f"- warning_count: {len(warnings)}\n"
        "Emit the ModuleContractQualityReviewRequest JSON only; "
        "do not re-audit manually."
    )
    if warnings:
        body += "\n\nWarnings:\n" + "\n".join(f"  - {w}" for w in warnings)

    update_agent_section(agent, _HEADER, body)


__all__ = ["run_module_contract_quality_gate"]


