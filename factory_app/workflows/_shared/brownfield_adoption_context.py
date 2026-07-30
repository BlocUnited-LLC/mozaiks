"""
Hook: Inject Brownfield Adoption Context

Fires as prompt middleware on planning agents in AgentGenerator and AppGenerator
when the active session is a brownfield build (brownfield_build_path is set).

Targeted agents:
  AgentGenerator  — PatternAgent, WorkflowBundleBuilderAgent
  AppGenerator    — InterviewAgent, AppPlanAgent

Reads from context variables set by ExistingAppDiscovery's
save_existing_app_artifacts tool:
  - brownfield_build_path    "light_integration" | "full_migration"
  - adoption_plan            canonical AdoptionPlan from discovery
  - ownership_boundary       ownership_boundary artifact (boundaries list)
  - brownfield_registration  BrownfieldRegistration record

Returns an empty string when brownfield_build_path is absent or null so there
is no prompt noise for greenfield builds.

Rule: agents must not propose changes to surfaces whose ownership_class is
read_only_discovered unless the user explicitly approves a staged patch.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_HEADER = "[BROWNFIELD ADOPTION CONTEXT]"


def _get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    try:
        if hasattr(context_variables, "get"):
            return context_variables.get(key, default)
        return context_variables[key]
    except (KeyError, TypeError):
        return default


def _format_adoption_plan(plan: dict[str, Any]) -> str:
    lines: list[str] = []
    path = plan.get("recommended_path") or plan.get("adoption_level") or ""
    if path:
        lines.append(f"  Recommended path: {path}")
    overlays = plan.get("candidate_overlays") or []
    if overlays:
        lines.append(f"  Candidate overlays ({len(overlays)}): {', '.join(str(o) for o in overlays[:8])}")
        if len(overlays) > 8:
            lines.append(f"    ... and {len(overlays) - 8} more")
    adapters = plan.get("candidate_adapters") or []
    if adapters:
        lines.append(f"  Candidate adapters: {', '.join(str(a) for a in adapters[:5])}")
    migrations = plan.get("candidate_migrations") or []
    if migrations:
        lines.append(f"  Migration candidates: {', '.join(str(m) for m in migrations[:5])}")
    decisions = plan.get("human_decisions_required") or []
    if decisions:
        lines.append("  Required human decisions:")
        for decision in decisions[:4]:
            lines.append(f"    - {decision}")
    not_in_scope = plan.get("not_in_scope") or []
    if not_in_scope:
        lines.append("  NOT in scope:")
        for item in not_in_scope[:3]:
            lines.append(f"    - {item}")
    return "\n".join(lines)


def _format_ownership_summary(boundary_artifact: dict[str, Any]) -> str:
    boundaries = boundary_artifact.get("ownership_boundaries") or []
    if not boundaries:
        return "  No ownership boundaries declared."

    class_counts: dict[str, int] = {}
    read_only_examples: list[str] = []
    overlay_examples: list[str] = []

    for b in boundaries:
        cls = b.get("ownership") or b.get("ownership_class") or "unknown"
        class_counts[cls] = class_counts.get(cls, 0) + 1
        path = b.get("path_or_artifact") or ""
        if cls == "read_only_discovered" and len(read_only_examples) < 4:
            read_only_examples.append(path)
        elif cls == "generated_overlay" and len(overlay_examples) < 4:
            overlay_examples.append(path)

    lines: list[str] = []
    for cls, count in sorted(class_counts.items()):
        lines.append(f"  {cls}: {count} surface(s)")

    if read_only_examples:
        lines.append(
            "  read_only examples (DO NOT modify without explicit approval): "
            + ", ".join(read_only_examples)
        )
    if overlay_examples:
        lines.append(
            "  overlay targets (safe to generate): "
            + ", ".join(overlay_examples)
        )
    return "\n".join(lines)


def inject_brownfield_adoption_context(
    agent_name: str | None = None,
    context_variables: Any = None,
    **_kwargs: Any,
) -> str:
    """Return a brownfield adoption context block for planning agents.

    Returns empty string for greenfield builds so there is no prompt noise.
    """
    build_path = _get(context_variables, "brownfield_build_path")
    if not build_path:
        return ""

    adoption_plan = _get(context_variables, "adoption_plan") or {}
    ownership_boundary = _get(context_variables, "ownership_boundary") or {}
    registration = _get(context_variables, "brownfield_registration") or {}

    app_id = (
        registration.get("app_id")
        or ownership_boundary.get("app_id")
        or adoption_plan.get("app_id")
        or "unknown"
    )

    sections: list[str] = [
        _HEADER,
        f"Build path: {build_path}",
        f"App: {app_id}",
    ]

    reg_status = registration.get("status") or ""
    if reg_status:
        sections.append(f"Registration status: {reg_status}")

    if adoption_plan:
        sections.append("")
        sections.append("Adoption Plan:")
        sections.append(_format_adoption_plan(adoption_plan))

    if ownership_boundary:
        sections.append("")
        sections.append("Ownership Boundaries:")
        sections.append(_format_ownership_summary(ownership_boundary))

    sections.append("")
    sections.append(
        "RULE: Agents must not propose code changes to read_only_discovered surfaces "
        "without explicit user approval. Generate only overlays, adapters, and workflows "
        "that the AdoptionPlan marks as candidate_overlays or candidate_adapters."
    )

    return "\n".join(sections)
