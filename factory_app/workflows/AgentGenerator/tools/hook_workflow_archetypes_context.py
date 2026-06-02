"""
Hook: Inject Workflow Archetype Library into WorkflowBundleBuilderAgent

Fires as an update_agent_state hook on WorkflowBundleBuilderAgent.

Closes Gap #7 (archetype library gap) — WorkflowBundleBuilderAgent previously had
access to the AG2 orchestration pattern guidance (Pipeline, Feedback Loop, etc.)
but not the higher-level workflow archetypes that define:
  - Canonical agent sequences for AI-native packs (ai_review, ai_analysis, ai_extraction)
  - Per-archetype hard constraints (startup_mode, result action contract, forbidden operations)
  - Orchestrator defaults (human_in_the_loop, max_turns, workflow_startup_mode)

When the worker's current_task has a capability_id ending in a known AI-native
suffix, this hook injects the matching archetype from workflow_archetypes.yaml as
[WORKFLOW ARCHETYPE: <name>] so the agent has the full agent sequence and
behavioral constraints, not just the AG2 pattern topology.

No-ops for non-AI-native workflows.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_TOOLS_DIR = Path(__file__).parent.parent.parent / "AppGenerator" / "tools"
_WORKFLOW_ARCHETYPES_PATH = _TOOLS_DIR / "workflow_archetypes.yaml"

_HEADER_PREFIX = "[WORKFLOW ARCHETYPE]"

_SUFFIX_TO_ARCHETYPE = {
    "-review-workflow": "ai_review",
    "-analysis-workflow": "ai_analysis",
    "-extraction-workflow": "ai_extraction",
}


def _load_archetypes() -> Dict[str, Any]:
    try:
        with _WORKFLOW_ARCHETYPES_PATH.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("archetypes", {}) if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("[hook_workflow_archetypes_context] Failed to load workflow_archetypes.yaml: %s", exc)
        return {}


def _detect_archetype_name(context_variables: Any) -> Optional[str]:
    """Return the archetype name for the current task if it is an AI-native workflow."""
    ctx: Dict[str, Any] = {}
    if hasattr(context_variables, "data"):
        ctx = context_variables.data
    elif isinstance(context_variables, dict):
        ctx = context_variables

    current_task = ctx.get("current_task") or {}
    if not isinstance(current_task, dict):
        return None

    capability_id = str(current_task.get("capability_id") or "").strip()
    if not capability_id:
        # Fall back to workflow name
        capability_id = str(current_task.get("name") or "").strip()
    if not capability_id:
        return None

    for suffix, archetype_name in _SUFFIX_TO_ARCHETYPE.items():
        if capability_id.endswith(suffix):
            return archetype_name
    return None


def _render_archetype_section(name: str, archetype: Dict[str, Any]) -> str:
    """Render a single archetype entry as readable guidance."""
    lines: List[str] = [
        f"You are generating a workflow bundle for an AI-native pack workflow.",
        f"The assigned archetype is: {name}",
        "",
    ]

    summary = str(archetype.get("summary", "")).strip()
    if summary:
        lines.append(f"SUMMARY: {summary}")
        lines.append("")

    startup_mode = str(archetype.get("startup_mode", "")).strip()
    if startup_mode:
        lines.append(f"startup_mode: {startup_mode}")

    orchestration_pattern = str(archetype.get("orchestration_pattern", "")).strip()
    if orchestration_pattern:
        lines.append(f"orchestration_pattern: {orchestration_pattern}")

    defaults = archetype.get("orchestrator_defaults") or {}
    if isinstance(defaults, dict) and defaults:
        lines.append("orchestrator_defaults:")
        for k, v in defaults.items():
            lines.append(f"  {k}: {v}")
    lines.append("")

    canon_seq = archetype.get("canonical_agent_sequence") or []
    if canon_seq:
        lines.append("CANONICAL AGENT SEQUENCE (follow exactly):")
        for entry in canon_seq:
            if isinstance(entry, dict):
                for agent_name, agent_spec in entry.items():
                    role = str(agent_spec.get("role", "")).strip() if isinstance(agent_spec, dict) else ""
                    mcr = agent_spec.get("max_consecutive_auto_reply") if isinstance(agent_spec, dict) else None
                    sout = agent_spec.get("structured_outputs_required") if isinstance(agent_spec, dict) else None
                    lines.append(f"  - {agent_name}:")
                    if role:
                        # Wrap role text
                        lines.append(f"      role: {role[:300]}")
                    if mcr is not None:
                        lines.append(f"      max_consecutive_auto_reply: {mcr}")
                    if sout is not None:
                        lines.append(f"      structured_outputs_required: {sout}")
        lines.append("")

    hard_constraints = archetype.get("hard_constraints") or []
    if hard_constraints:
        lines.append("HARD CONSTRAINTS (enforced — never violate):")
        for hc in hard_constraints:
            lines.append(f"  - {str(hc).strip()}")
        lines.append("")

    task_batches = archetype.get("task_batches_required")
    if task_batches:
        lines.append("task_batches.yaml is REQUIRED for this archetype (parallel worker dispatch).")
        lines.append("")

    return "\n".join(lines)


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
            "[WorkflowBundleBuilderAgent] Failed to inject archetype section: %s", exc
        )


def inject_workflow_archetypes_context(
    agent: Any,
    messages: List[Dict[str, Any]],
) -> None:
    """
    update_agent_state hook for WorkflowBundleBuilderAgent.

    Injects [WORKFLOW ARCHETYPE: <name>] from workflow_archetypes.yaml when the
    current task is an AI-native pack workflow (capability_id ending in
    -review-workflow, -analysis-workflow, or -extraction-workflow).

    No-ops for all other workflows and all other agents.
    """
    if getattr(agent, "name", "") != "WorkflowBundleBuilderAgent":
        return

    context_variables = getattr(agent, "context_variables", None)
    archetype_name = _detect_archetype_name(context_variables)
    if not archetype_name:
        return

    archetypes = _load_archetypes()
    archetype = archetypes.get(archetype_name)
    if not isinstance(archetype, dict):
        logger.warning(
            "[WorkflowBundleBuilderAgent] Archetype %r not found in workflow_archetypes.yaml",
            archetype_name,
        )
        return

    header = f"{_HEADER_PREFIX}: {archetype_name}"
    body = _render_archetype_section(archetype_name, archetype)
    _inject_section(agent, header, body)

    logger.info(
        "[WorkflowBundleBuilderAgent] Injected workflow archetype %r from archetype library",
        archetype_name,
    )


__all__ = ["inject_workflow_archetypes_context"]
