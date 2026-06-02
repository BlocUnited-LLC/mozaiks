"""
Hook: Inject AI Pack Archetype and Callback Context

Fires as an update_agent_state hook on PatternAgent and WorkflowBundleBuilderAgent.

Closes Gap #5 (async event callback loop) — ensures that AI-native pack workflows
generate:
  1. A module_interface.yaml declaring the callback module action
  2. A backend_request tool declaration in tools.yaml
  3. ResultAgent instructions to POST the result to /api/modules/{module_id}/{action_id}

**PatternAgent** — detects AI-native workflow surfaces in design_surface_map and
injects [AI PACK ARCHETYPE CONTEXT] so PatternAgent:
  - Uses the fixed archetype + startup_mode instead of free-form selection
  - Writes a rich initial_message for the WorkflowBundleBuilderAgent worker that
    includes module_id, callback_endpoint, and explicit generation requirements

**WorkflowBundleBuilderAgent** — if any AI-native workflow surfaces exist in
design_surface_map, injects [AI PACK CALLBACK CONTRACT] so any worker generating
an AI-native pack workflow knows how to generate module_interface.yaml and declare
the backend_request tool.

Both injections are conditional — no-op when no AI-native workflow surfaces are
detected in design_surface_map.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

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
        "startup_mode": "BackendOnly",
        "agents": "IntakeAgent → ReviewerAgent → ResultAgent",
        "result_action": "record_review_result",
        "result_agent": "ResultAgent",
    },
    _ANALYSIS_SUFFIX: {
        "archetype": "ai_analysis",
        "pattern": "Pipeline",
        "startup_mode": "BackendOnly",
        "agents": "ContextReaderAgent → AnalysisAgent → ResultWriterAgent",
        "result_action": "store_analysis_result",
        "result_agent": "ResultWriterAgent",
    },
    _EXTRACTION_SUFFIX: {
        "archetype": "ai_extraction",
        "pattern": "Triage with Tasks",
        "startup_mode": "BackendOnly",
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
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
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
                        **archetype_info,
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
            setattr(agent, "_system_message", new_message)
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
        "Use the archetype, startup_mode, and initial_message template below for each.\n",
        "RULES FOR AI-NATIVE WORKFLOWS:\n"
        "  - role: supporting (all AI-native workflows run in the background)\n"
        "  - startup_mode: BackendOnly (no user chat session)\n"
        "  - human_in_the_loop: false\n"
        "  - The initial_message MUST include module_id, callback_action, and callback_endpoint\n"
        "    so the WorkflowBundleBuilderAgent worker knows the full return-path contract.\n",
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
        callback_endpoint = f"POST /api/modules/{module_id}/{result_action}"

        initial_msg = (
            f"Generate a BackendOnly {archetype} workflow bundle. "
            f"capability_id={cap_id}, workflow_name={_to_pascal(cap_id)}. "
            f"Use the {pattern} orchestration pattern with agent sequence: {agents_seq}. "
            f"startup_mode=BackendOnly, human_in_the_loop=false. "
            f"CALLBACK CONTRACT: the {result_agent} MUST call backend_request "
            f"(POST {callback_endpoint}) to write the result back to the owning module. "
            f"Declare backend_request from mozaiksai.core.workflow.app_backend_tools in tools.yaml. "
            f"Generate module_interface.yaml at the workflow root declaring "
            f"module_id={module_id}, action_id={result_action}. "
        )
        if tb:
            initial_msg += "task_batches.yaml is required for parallel worker dispatch. "

        block = (
            f"capability_id: {cap_id}\n"
            f"  module_id: {module_id}\n"
            f"  archetype: {archetype}\n"
            f"  pattern: {pattern}\n"
            f"  startup_mode: BackendOnly\n"
            f"  agent_sequence: {agents_seq}\n"
            f"  result_agent: {result_agent}\n"
            f"  result_callback_action: {result_action}\n"
            f"  callback_endpoint: {callback_endpoint}\n"
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

_MODULE_INTERFACE_TEMPLATE = """schema_version: mozaiks.module_interface.v1
module_actions:
  - module_id: {module_id}
    action_id: {result_action}
    description: >
      Write the AI {archetype_label} result back to the owning module.
      Called by {result_agent} after processing completes.
"""

_TOOLS_YAML_ENTRY = """  - name: backend_request
    type: Agent_Tool
    module: mozaiksai.core.workflow.app_backend_tools
    function: backend_request
    description: >
      Make an HTTP request to the app backend module action API.
      Use to call POST /api/modules/{module_id}/{result_action} to write results.
"""


def _build_callback_body(ai_surfaces: list[dict]) -> str:
    lines: list[str] = [
        "If your initial_message identifies this workflow as an AI-native pack workflow "
        "(capability_id ending in -review-workflow, -analysis-workflow, or -extraction-workflow), "
        "you MUST follow this callback contract in addition to your standard bundle generation.\n",
        "REQUIRED FILES for AI-native pack workflows:\n"
        "\n"
        "1. module_interface.yaml (at workflow root)\n"
        "   Declares the module action this workflow calls to write results back.\n"
        "   Format:\n"
        "   ```yaml\n"
        "   schema_version: mozaiks.module_interface.v1\n"
        "   module_actions:\n"
        "     - module_id: {module_id}   # from your initial_message\n"
        "       action_id: {result_action}  # from your initial_message\n"
        "       description: Write the AI result back to the owning module\n"
        "   ```\n"
        "\n"
        "2. backend_request tool in tools.yaml\n"
        "   Add this entry to the tools[] list:\n"
        "   ```yaml\n"
        "   - name: backend_request\n"
        "     type: Agent_Tool\n"
        "     module: mozaiksai.core.workflow.app_backend_tools\n"
        "     function: backend_request\n"
        "   ```\n"
        "   Assign backend_request to the {result_agent} only.\n"
        "   Do NOT generate a Python stub for backend_request — it is a platform tool.\n"
        "\n"
        "3. {result_agent} instructions in agents.yaml\n"
        "   The result/writer/synthesis agent MUST:\n"
        "   - Call backend_request(method='POST', path='/api/modules/{module_id}/{result_action}',\n"
        "     payload={...result fields...})\n"
        "   - Confirm success before terminating\n"
        "   - Terminate to 'terminate' (BackendOnly — no user reply)\n"
        "\n"
        "4. No Python stub for backend_request\n"
        "   backend_request is provided by mozaiksai.core.workflow.app_backend_tools.\n"
        "   Only generate stubs for workflow-local custom tools.\n",
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
            "\nMatch your initial_message's capability_id against the list above to find "
            "the exact module_id and result_action for your module_interface.yaml.\n"
        )

    return "\n".join(lines)


# =============================================================================
# Hook entry point
# =============================================================================

def inject_ai_pack_archetype_context(
    agent: Any,
    messages: List[Dict[str, Any]],
) -> None:
    """
    update_agent_state hook for PatternAgent and WorkflowBundleBuilderAgent.

    PatternAgent: injects [AI PACK ARCHETYPE CONTEXT] with fixed archetype,
    startup_mode, and enriched initial_message templates.

    WorkflowBundleBuilderAgent: injects [AI PACK CALLBACK CONTRACT] so any
    worker generating an AI-native pack workflow knows to generate
    module_interface.yaml and declare backend_request.

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
