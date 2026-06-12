"""
Hook: Inject AI Pack Workflow Context

Fires as an prompt middleware function on AppPlanAgent.

When the current plan (or concept) includes any AI-native capability packs
(ai_review_pack, ai_analysis_pack, ai_extraction_pack), this hook injects an
[AI PACK WORKFLOW CONTEXT] block into AppPlanAgent's system message.

The block provides:
  - Precise build task patterns for each detected AI pack
  - The capability_id naming convention for reactions.yaml wiring
  - Hard constraints preventing the common mistake of emitting surface_kind='workflow'
    tasks (which the AppBuildPlan validator rejects)
  - The integration bridge: how the module's reactions.yaml routes domain events
    to the separately-generated AgentGenerator workflow

This hook is conditional — it no-ops when no AI-native pack is present in the
concept or plan context, so it does not add noise to apps that don't need it.
"""

from __future__ import annotations

import logging
from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section

logger = logging.getLogger(__name__)

_AI_NATIVE_PACKS = frozenset({"ai_review_pack", "ai_analysis_pack", "ai_extraction_pack"})
_HEADER = "[AI PACK WORKFLOW CONTEXT]"

def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _detect_ai_packs(context_variables: Any) -> list[str]:
    """Return the list of AI-native pack ids present in the plan or concept context."""
    detected: list[str] = []

    # Check app_build_plan.capability_packs
    plan = _context_get(context_variables, "app_build_plan") or {}
    if isinstance(plan, dict):
        for pack in plan.get("capability_packs") or []:
            if isinstance(pack, dict):
                pack_type = str(pack.get("pack_type") or "").strip()
                if pack_type in _AI_NATIVE_PACKS:
                    detected.append(pack_type)

    # Check concept_blueprint.capability_pack_hints (ValueEngine output)
    blueprint = _context_get(context_variables, "concept_blueprint") or {}
    if isinstance(blueprint, dict):
        for hint in blueprint.get("capability_pack_hints") or []:
            hint_str = str(hint or "").strip()
            if hint_str in _AI_NATIVE_PACKS and hint_str not in detected:
                detected.append(hint_str)

    return detected


def _pack_instructions(pack_id: str) -> str:
    """Return directive build instructions for a single AI-native pack."""
    if pack_id == "ai_review_pack":
        return (
            "ai_review_pack detected.\n"
            "\n"
            "HARD CONSTRAINTS:\n"
            "1. Do NOT emit a build task with surface_kind='workflow'. "
            "Workflow surfaces are AgentGenerator-owned and the validator will reject them.\n"
            "2. DO emit these module-side build tasks for the module that owns the reviewed entity:\n"
            "   a. module_contract task: add actions submit_for_review (emits "
            "domain.{module_id}.submitted, sets status=under_review) and "
            "record_review_result (accepts decision: approved|rejected|revision_requested "
            "and feedback: str, updates status and stores feedback).\n"
            "      owned_paths must include:\n"
            "        modules/{module_id}/contracts/events.yaml  "
            "(declares domain.{module_id}.submitted)\n"
            "        modules/{module_id}/contracts/reactions.yaml  "
            "(routes event to AI workflow)\n"
            "   b. data_models and business_services tasks: include review_status and "
            "review_feedback fields in the entity schema.\n"
            "3. reactions.yaml entry must be:\n"
            "   event_type: domain.{module_id}.submitted\n"
            "   target.kind: capability\n"
            "   target.capability_id: {module_id}-review-workflow\n"
            "4. Add a workflow_touchpoint on the module page for submit_for_review "
            "(placement: row_action or primary_button).\n"
            "5. Add an event_flow entry:\n"
            "   event_type: domain.{module_id}.submitted\n"
            "   workflow_capability_ids: [\"{module_id}-review-workflow\"]\n"
            "6. Set agent_backend_required: true on the AppBuildPlan.\n"
            "7. The AI review workflow bundle is generated in a separate AgentGenerator run "
            "using the ai_review archetype, capability_id={module_id}-review-workflow, "
            "workflow_startup_mode=BackendOnly.\n"
        )

    if pack_id == "ai_analysis_pack":
        return (
            "ai_analysis_pack detected.\n"
            "\n"
            "HARD CONSTRAINTS:\n"
            "1. Do NOT emit a build task with surface_kind='workflow'.\n"
            "2. DO emit these module-side build tasks for the module that owns the analyzed entity:\n"
            "   a. module_contract task: add action store_analysis_result (accepts structured "
            "analysis object and writes to record). Ensure the creation event is declared.\n"
            "      owned_paths must include:\n"
            "        modules/{module_id}/contracts/events.yaml  "
            "(declares domain.{module_id}.created)\n"
            "        modules/{module_id}/contracts/reactions.yaml  "
            "(routes event to AI workflow)\n"
            "   b. data_models task: add analysis_result subdocument field to the entity schema.\n"
            "3. reactions.yaml entry must be:\n"
            "   event_type: domain.{module_id}.created\n"
            "   target.kind: capability\n"
            "   target.capability_id: {module_id}-analysis-workflow\n"
            "4. Do NOT add a workflow_touchpoint — this workflow runs BackendOnly with no "
            "user-initiated launch.\n"
            "5. Add an event_flow entry:\n"
            "   event_type: domain.{module_id}.created\n"
            "   workflow_capability_ids: [\"{module_id}-analysis-workflow\"]\n"
            "6. Set agent_backend_required: true on the AppBuildPlan.\n"
            "7. The AI analysis workflow bundle is generated in a separate AgentGenerator run "
            "using the ai_analysis archetype, capability_id={module_id}-analysis-workflow, "
            "workflow_startup_mode=BackendOnly.\n"
        )

    if pack_id == "ai_extraction_pack":
        return (
            "ai_extraction_pack detected.\n"
            "\n"
            "HARD CONSTRAINTS:\n"
            "1. Do NOT emit a build task with surface_kind='workflow'.\n"
            "2. DO emit these module-side build tasks for the module that owns the batch entity:\n"
            "   a. module_contract task: add actions:\n"
            "      - process_batch: accepts batch_id, emits domain.{module_id}.batch_submitted\n"
            "      - store_extraction_results: accepts batch_id and results array, "
            "writes to records\n"
            "      owned_paths must include:\n"
            "        modules/{module_id}/contracts/events.yaml  "
            "(declares domain.{module_id}.batch_submitted)\n"
            "        modules/{module_id}/contracts/reactions.yaml  "
            "(routes event to AI workflow)\n"
            "   b. data_models task: add extraction_results array field to item schema.\n"
            "3. reactions.yaml entry must be:\n"
            "   event_type: domain.{module_id}.batch_submitted\n"
            "   target.kind: capability\n"
            "   target.capability_id: {module_id}-extraction-workflow\n"
            "4. Add a workflow_touchpoint on the batch upload page for process_batch "
            "(placement: primary_button).\n"
            "5. Add an event_flow entry:\n"
            "   event_type: domain.{module_id}.batch_submitted\n"
            "   workflow_capability_ids: [\"{module_id}-extraction-workflow\"]\n"
            "6. Set agent_backend_required: true on the AppBuildPlan.\n"
            "7. The AI extraction workflow bundle is generated in a separate AgentGenerator run "
            "using the ai_extraction archetype, capability_id={module_id}-extraction-workflow, "
            "workflow_startup_mode=BackendOnly with task_batches for parallel workers.\n"
        )

    return ""


def _build_body(detected_packs: list[str]) -> str:
    parts: list[str] = [
        "The following AI-native capability packs are in scope for this app. "
        "Each pack wires a deterministic module to an autonomous AI workflow "
        "via domain events and reactions.yaml. Follow the constraints below precisely.\n",
        "KEY RULE: AI pack workflow bundles are generated by AgentGenerator in a "
        "SEPARATE build run — they are NOT AppGenerator build tasks. "
        "AppGenerator's job is to generate the MODULE SIDE only: the actions, "
        "events, reactions.yaml wiring, and event_flows declarations.\n",
        "capability_id naming convention:\n"
        "  ai_review_pack   → {module_id}-review-workflow\n"
        "  ai_analysis_pack → {module_id}-analysis-workflow\n"
        "  ai_extraction_pack → {module_id}-extraction-workflow\n"
        "Use this convention in reactions.yaml and event_flows so the platform "
        "routes domain events to the correct AgentGenerator workflow.\n",
    ]

    for pack_id in detected_packs:
        instr = _pack_instructions(pack_id)
        if instr:
            parts.append(f"--- {pack_id} ---\n{instr}")

    return "\n".join(parts)


def inject_ai_pack_workflow_context(
    agent: Any,
    messages: list[dict[str, Any]],
) -> None:
    """
    prompt middleware function for AppPlanAgent.

    Detects AI-native capability packs in the plan or concept context.
    When found, injects [AI PACK WORKFLOW CONTEXT] with precise build task
    instructions and hard constraints for each pack.

    No-ops when no AI-native packs are detected.
    """
    agent_name = getattr(agent, "name", "")
    if agent_name != "AppPlanAgent":
        return

    context_variables = getattr(agent, "context_variables", None)

    try:
        detected = _detect_ai_packs(context_variables)
        if not detected:
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



