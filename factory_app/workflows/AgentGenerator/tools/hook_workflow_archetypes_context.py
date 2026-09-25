"""
Hook: Inject Workflow Archetype Library into WorkflowBundleBuilderAgent.

Fires as a prompt middleware function on WorkflowBundleBuilderAgent.

WorkflowBundleBuilderAgent receives AG2 orchestration pattern guidance and the
higher-level workflow archetypes that define:
  - Canonical agent sequences for AI-native packs (ai_review, ai_analysis, ai_extraction)
  - Per-archetype hard constraints (workflow_startup_mode, result action contract, forbidden operations)
  - Orchestrator defaults (human_in_the_loop, max_turns, workflow_startup_mode)

The worker's current_task is a WorkflowInPack item. That model forbids extra
fields, so it never carries a capability_id; the capability id lives on the
design surface the workflow implements. PatternAgent names each AI-native pack
workflow `_to_pascal(capability_id)` (hook_ai_pack_archetype_context), and the
worker receives design_surface_map through its task-batch context fields, so
this hook finds the declared workflow surface whose trigger maps to the task's
name and injects the matching archetype from workflow_archetypes.yaml as
[WORKFLOW ARCHETYPE: <name>], so the agent has the full agent sequence and
behavioral constraints, not just the AG2 pattern topology.

No-ops for non-AI-native workflows.
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from factory_app.workflows._shared.hook_utils import workflow_context_path
from mozaiksai.core.workflow.context.frozen import detach

logger = logging.getLogger(__name__)

_WORKFLOW_ARCHETYPES_PATH = workflow_context_path("AppGenerator", "workflow_archetypes.yaml")

_HEADER_PREFIX = "[WORKFLOW ARCHETYPE]"

_SUFFIX_TO_ARCHETYPE = {
    "-review-workflow": "ai_review",
    "-analysis-workflow": "ai_analysis",
    "-extraction-workflow": "ai_extraction",
}


def _load_archetypes() -> dict[str, Any]:
    try:
        with _WORKFLOW_ARCHETYPES_PATH.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("archetypes", {}) if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("[hook_workflow_archetypes_context] Failed to load workflow_archetypes.yaml: %s", exc)
        return {}


def _to_pascal(capability_id: str) -> str:
    """The workflow name PatternAgent is told to use for an AI-native capability id.

    Mirrors hook_ai_pack_archetype_context._to_pascal, which instructs PatternAgent
    `workflow_name={_to_pascal(cap_id)}` for exactly these workflows.
    """
    return "".join(part.capitalize() for part in capability_id.split("-"))


def _declared_workflow_triggers(design_surface_map: Any) -> list[str]:
    if not isinstance(design_surface_map, dict):
        return []
    triggers: list[str] = []
    for surface in design_surface_map.get("surfaces") or []:
        if not isinstance(surface, dict) or surface.get("surface_kind") != "workflow":
            continue
        triggers.extend(str(t or "").strip() for t in surface.get("workflow_triggers") or [])
    return [t for t in triggers if t]


def _detect_archetype_name(context_variables: Any) -> str | None:
    """Return the archetype for the current task when it implements a declared AI-native surface."""
    # No live container exposes `.data` (#300 renamed the bridge's store), and
    # every one freezes reads, so read through `get` and detach before type-testing.
    getter = getattr(context_variables, "get", None)
    if not callable(getter):
        return None
    current_task = detach(getter("current_task"))
    if not isinstance(current_task, dict):
        return None
    workflow_name = str(current_task.get("name") or "").strip()
    if not workflow_name:
        return None

    for capability_id in _declared_workflow_triggers(detach(getter("design_surface_map"))):
        if _to_pascal(capability_id) != workflow_name:
            continue
        for suffix, archetype_name in _SUFFIX_TO_ARCHETYPE.items():
            if capability_id.endswith(suffix):
                return archetype_name
    return None


def _render_archetype_section(name: str, archetype: dict[str, Any]) -> str:
    """Render a single archetype entry as readable guidance."""
    lines: list[str] = [
        "You are generating a workflow bundle for an AI-native pack workflow.",
        f"The assigned archetype is: {name}",
        "",
    ]

    summary = str(archetype.get("summary", "")).strip()
    if summary:
        lines.append(f"SUMMARY: {summary}")
        lines.append("")

    workflow_startup_mode = str(archetype.get("workflow_startup_mode", "")).strip()
    if workflow_startup_mode:
        lines.append(f"workflow_startup_mode: {workflow_startup_mode}")

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
            agent._system_message = new_message
    except Exception as exc:
        logger.error(
            "[WorkflowBundleBuilderAgent] Failed to inject archetype section: %s", exc
        )


def inject_workflow_archetypes_context(
    agent: Any,
    messages: list[dict[str, Any]],
) -> None:
    """
    prompt middleware function for WorkflowBundleBuilderAgent.

    Injects [WORKFLOW ARCHETYPE: <name>] from workflow_archetypes.yaml when the
    current task implements a declared AI-native workflow surface (capability id
    ending in -review-workflow, -analysis-workflow, or -extraction-workflow).

    No-ops for all other workflows and all other agents. Until #723 it keyed on
    current_task.capability_id, a field WorkflowInPack forbids, so it never fired.
    """
    if getattr(agent, "name", "") != "WorkflowBundleBuilderAgent":
        return

    context_variables = getattr(agent, "context_variables", None)
    archetype_name = _detect_archetype_name(context_variables)
    if not archetype_name:
        getter = getattr(context_variables, "get", None)
        task = detach(getter("current_task")) if callable(getter) else None
        logger.info(
            "WORKFLOW_ARCHETYPE_CONTEXT skipped agent=WorkflowBundleBuilderAgent reason=no_declared_ai_surface "
            "workflow_name=%s",
            (task or {}).get("name") if isinstance(task, dict) else None,
        )
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



