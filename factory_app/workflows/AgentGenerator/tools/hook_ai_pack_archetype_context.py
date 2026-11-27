"""Project AI capability archetypes and approved automatic result writes into AgentGenerator."""

from __future__ import annotations

import logging
from typing import Any

from mozaiksai.core.workflow.context.frozen import detach

logger = logging.getLogger(__name__)

_REVIEW_SUFFIX = "-review-workflow"
_ANALYSIS_SUFFIX = "-analysis-workflow"
_EXTRACTION_SUFFIX = "-extraction-workflow"

_ARCHETYPE_HEADER = "[AI PACK ARCHETYPE CONTEXT]"
_CALLBACK_HEADER = "[AI PACK CALLBACK CONTRACT]"

_ARCHETYPE_MAP = {
    _REVIEW_SUFFIX: {
        "archetype": "ai_review",
        "pattern": "Feedback Loop",
        "workflow_startup_mode": "BackendOnly",
        "agents": "IntakeAgent → ReviewerAgent → ResultAgent",
        "result_action": "record_review_result",
        "result_agent": "ResultAgent",
    },
    _ANALYSIS_SUFFIX: {
        "archetype": "ai_analysis",
        "pattern": "Pipeline",
        "workflow_startup_mode": "BackendOnly",
        "agents": "ContextReaderAgent → AnalysisAgent → ResultWriterAgent",
        "result_action": "store_analysis_result",
        "result_agent": "ResultWriterAgent",
    },
    _EXTRACTION_SUFFIX: {
        "archetype": "ai_extraction",
        "pattern": "Triage with Tasks",
        "workflow_startup_mode": "BackendOnly",
        "agents": "TriageAgent → ExtractionWorkerAgent (task_batches) → SynthesisAgent",
        "result_action": "store_extraction_results",
        "result_agent": "SynthesisAgent",
        "task_batches_required": True,
    },
}


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


def _detect_ai_workflow_surfaces(context_variables: Any) -> list[dict]:
    """Return AI workflow surface descriptors from design_surface_map, with derived module_id."""
    found: list[dict] = []
    surface_map = _context_get(context_variables, "design_surface_map") or {}
    if not isinstance(surface_map, dict):
        return found

    surfaces = surface_map.get("surfaces") or []
    for surface in surfaces:
        if not isinstance(surface, dict):
            continue
        if surface.get("surface_kind") != "workflow":
            continue
        for trigger in surface.get("workflow_triggers") or []:
            trigger_str = str(trigger or "").strip()
            for suffix, archetype_info in _ARCHETYPE_MAP.items():
                if trigger_str.endswith(suffix):
                    # Derive module_id by stripping the suffix
                    # "proposals-review-workflow" → "proposals"
                    module_id = trigger_str[: -len(suffix)]
                    found.append({
                        "surface_id": surface.get("surface_id", ""),
                        "capability_id": trigger_str,
                        "module_id": module_id,
                        "suffix": suffix,
                        **archetype_info,  # type: ignore[dict-item]
                    })
                    break

    return found


def _inject_section(agent: Any, header: str, body: str) -> None:
    """Append or replace a named section in the agent system message."""
    try:
        current: str = (
            getattr(agent, "system_message", None)
            or getattr(agent, "_system_message", "")
            or ""
        )
        section = f"{header}\n{body}"
        if header in current:
            pre, _, rest = current.partition(header)
            next_idx = rest.find("\n\n[")
            after = rest[next_idx:] if next_idx > 0 else ""
            new_message = f"{pre.rstrip()}\n\n{section}{after}"
        else:
            new_message = f"{current}\n\n{section}" if current else section

        if new_message == current:
            return

        updater = getattr(agent, "update_system_message", None)
        if callable(updater):
            updater(new_message)
        elif hasattr(agent, "_system_message"):
            agent._system_message = new_message
        else:
            agent._system_message = new_message
    except Exception as exc:
        logger.error(
            "[%s] Failed to inject section %s: %s",
            getattr(agent, "name", "?"),
            header,
            exc,
        )


# =============================================================================
# PatternAgent — archetype + initial_message template
# =============================================================================

def _build_pattern_body(ai_surfaces: list[dict]) -> str:
    lines: list[str] = [
        "The design_surface_map contains AI-native workflow surfaces. "
        "These use FIXED archetypes — do NOT apply free-form pattern selection. "
        "Use the archetype, workflow_startup_mode, and initial_message template below for each.\n",
        "RULES FOR AI-NATIVE WORKFLOWS:\n"
        "  - role: supporting (all AI-native workflows run in the background)\n"
        "  - workflow_startup_mode: BackendOnly (no user chat session)\n"
        "  - human_in_the_loop: false\n"
        "  - For a requested automatic write on private record creation, declare result_write_plans in "
        "WorkflowInPack and use the actual module actions, event and collection from the app design.\n",
    ]

    for s in ai_surfaces:
        cap_id = s["capability_id"]
        module_id = s["module_id"]
        archetype = s["archetype"]
        pattern = s["pattern"]
        agents_seq = s["agents"]
        result_action = s["result_action"]
        result_agent = s["result_agent"]
        tb = s.get("task_batches_required", False)

        initial_msg = (
            f"Generate a BackendOnly {archetype} workflow bundle. "
            f"capability_id={cap_id}, workflow_name={_to_pascal(cap_id)}. "
            f"Use the {pattern} orchestration pattern with agent sequence: {agents_seq}. "
            f"workflow_startup_mode=BackendOnly, human_in_the_loop=false. "
            f"Archetype example: {module_id}/{result_action}. For an approved private record insert, "
            f"honor the reviewed result_write_plans and give its named agent the exact result-field model. "
            f"Require the actual collection, stable record key, and source action from the build. "
        )
        if tb:
            initial_msg += "task_batches.yaml is required for parallel worker dispatch. "

        block = (
            f"capability_id: {cap_id}\n"
            f"  module_id: {module_id}\n"
            f"  archetype: {archetype}\n"
            f"  pattern: {pattern}\n"
            f"  workflow_startup_mode: BackendOnly\n"
            f"  agent_sequence: {agents_seq}\n"
            f"  result_agent: {result_agent}\n"
            f"  example_result_action: {result_action}\n"
        )
        if tb:
            block += "  task_batches_required: true\n"
        block += f"\n  initial_message to use:\n  \"{initial_msg}\"\n"
        lines.append(block)

    return "\n".join(lines)


def _to_pascal(capability_id: str) -> str:
    """Convert 'proposals-review-workflow' → 'ProposalsReviewWorkflow'."""
    return "".join(part.capitalize() for part in capability_id.split("-"))


# =============================================================================
# WorkflowBundleBuilderAgent — callback contract
# =============================================================================

def _build_callback_body(ai_surfaces: list[dict]) -> str:
    lines = [
        "For an automatic write to the triggering private record, use the result_write_plans in the "
        "reviewed workflow task. The result agent returns a validated model with exactly the approved "
        "fields. Factory materializes tools/save_workflow_result.py and its automatic, unbound tool. "
        "Declare the real event, owning module, acknowledged inserting source action, private collection, "
        "stable record key and internal result action. Each supported event must have one binding. "
        "A read-only workflow has an empty result plan. If the source does not authorize the required "
        "write, request the missing design decision. Never send a bearer token or arbitrary HTTP path as "
        "the callback, or invent a source action or schema field."
    ]

    # Add per-surface details for context
    if ai_surfaces:
        lines.append("AI-native workflow surfaces detected in this app's design_surface_map:")
        for s in ai_surfaces:
            lines.append(
                f"  capability_id: {s['capability_id']}  "
                f"module_id: {s['module_id']}  "
                f"result_action: {s['result_action']}  "
                f"result_agent: {s['result_agent']}"
            )
        lines.append(
            "\nUse the exact module, action and result fields from the reviewed task. "
            "The archetype examples above describe possible workflow shapes.\n"
        )

    return "\n".join(lines)


# =============================================================================
# Hook entry point
# =============================================================================

def inject_ai_pack_archetype_context(
    agent: Any,
    messages: list[dict[str, Any]],
) -> None:
    """
    prompt middleware function for PatternAgent and WorkflowBundleBuilderAgent.

    PatternAgent: injects [AI PACK ARCHETYPE CONTEXT] with fixed archetype,
    workflow_startup_mode, and enriched initial_message templates.

    WorkflowBundleBuilderAgent: injects [AI PACK CALLBACK CONTRACT] so any
    worker generating an AI-native pack workflow uses the reviewed result fields.

    No-ops when no AI-native workflow surfaces are found.
    """
    agent_name = getattr(agent, "name", "")
    if agent_name not in ("PatternAgent", "WorkflowBundleBuilderAgent"):
        return

    context_variables = getattr(agent, "context_variables", None)

    try:
        ai_surfaces = _detect_ai_workflow_surfaces(context_variables)
        if not ai_surfaces:
            return

        if agent_name == "PatternAgent":
            body = _build_pattern_body(ai_surfaces)
            _inject_section(agent, _ARCHETYPE_HEADER, body)
            logger.info(
                "[PatternAgent] Injected AI pack archetype context for: %s",
                ", ".join(s["capability_id"] for s in ai_surfaces),
            )

        elif agent_name == "WorkflowBundleBuilderAgent":
            body = _build_callback_body(ai_surfaces)
            _inject_section(agent, _CALLBACK_HEADER, body)
            logger.info(
                "[WorkflowBundleBuilderAgent] Injected AI pack callback contract "
                "(%d AI workflow(s) in scope)",
                len(ai_surfaces),
            )

    except Exception as exc:
        logger.error(
            "[%s] Failed to inject AI pack archetype/callback context: %s",
            agent_name,
            exc,
        )


__all__ = ["inject_ai_pack_archetype_context"]

