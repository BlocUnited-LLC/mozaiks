"""Project module design guidance for the AI capability pack archetypes selected by the app."""

from __future__ import annotations

import logging
from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section
from factory_app.workflows.AppGenerator.tools.hook_capability_routing_context import _load_routing
from mozaiksai.core.workflow.context.frozen import detach

logger = logging.getLogger(__name__)

_HEADER = "[AI PACK WORKFLOW CONTEXT]"


def _module_guidance() -> dict[str, str]:
    layers = (_load_routing() or {}).get("layers", {})
    return {pack["id"]: pack["module_guidance"].strip() for pack in layers.get("ai_native_packs", {}).get("packs", [])}


def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        # Live containers freeze reads; callers type-test the result as a dict.
        try:
            return detach(getter(key, default))
        except TypeError:
            value = getter(key)
            return default if value is None else detach(value)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _detect_ai_packs(context_variables: Any) -> list[str]:
    """Return advisory archetypes from the normalized app build plan."""
    detected: list[str] = []
    packs = _module_guidance()

    # Check app_build_plan.capability_packs
    plan = _context_get(context_variables, "app_build_plan") or {}
    if isinstance(plan, dict):
        for pack in plan.get("capability_packs") or []:
            if isinstance(pack, dict):
                pack_type = str(pack.get("pack_type") or "").strip()
                if pack_type in packs and pack_type not in detected:
                    detected.append(pack_type)

    return detected


def _pack_instructions(pack_id: str) -> str:
    advice = _module_guidance().get(pack_id)
    return f"{pack_id} detected.\n{advice}" if advice else ""


def _build_body(detected_packs: list[str]) -> str:
    parts = [
        "AppGenerator plans the module actions, data and committed events in approved scope. "
        "AgentGenerator owns workflow bundles. A pack archetype is design advice.",
        "Use only the named event and capability_id pairs in [WORKFLOW INTEGRATION CONTRACT] "
        "for workflow reactions and event_flows[].workflow_capability_ids. With no supplied binding, "
        "leave those workflow targets empty and identify the workflow requirement for AgentGenerator. "
        "Plan direct workflow launches only for approved user-facing workflows. "
        "A workflow with workflow_startup_mode=BackendOnly runs in the background.",
    ]
    for pack_id in detected_packs:
        instructions = _pack_instructions(pack_id)
        if instructions:
            parts.append(f"--- {pack_id} ---\n{instructions}")
    return "\n\n".join(parts)


def inject_ai_pack_workflow_context(
    agent: Any,
    messages: list[dict[str, Any]],
) -> None:
    """Add module design guidance for the archetypes in the saved plan."""
    agent_name = getattr(agent, "name", "")
    if agent_name != "AppPlanAgent":
        return

    context_variables = getattr(agent, "context_variables", None)

    try:
        detected = _detect_ai_packs(context_variables)
        if not detected:
            if _HEADER in agent.system_message:
                update_agent_section(agent, _HEADER, "The saved app build plan specifies no AI module archetypes.")
            return

        body = _build_body(detected)
        update_agent_section(agent, _HEADER, body)

        logger.info(
            "[%s] Injected AI pack workflow context for packs: %s",
            agent_name,
            ", ".join(detected),
        )

    except Exception as exc:
        logger.error(
            "[%s] Failed to inject AI pack workflow context: %s",
            agent_name,
            exc,
        )


__all__ = ["inject_ai_pack_workflow_context"]
