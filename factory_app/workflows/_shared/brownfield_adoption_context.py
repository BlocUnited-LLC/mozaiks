"""
Prompt middleware for Existing App Enhancement planning.

This middleware gives planning agents concise context about an existing
application the user wants to enhance with Mozaiks. It keeps the platform's
internal context keys stable while presenting the prompt with product language
about AI workflows, app connections, protected app areas, and approved feature
work.

Internal implementation details still use names such as `brownfield_build_path`,
`adoption_plan`, `ownership_boundary`, and `brownfield_registration` because
other platform contracts depend on them. Those values are mapped into
product-facing labels before the prompt block is generated.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_HEADER = "[EXISTING APP ENHANCEMENT]"

_SECTION_LABELS = {
    "enhancement": "Enhancement",
    "connected_app": "Connected App",
    "connection_status": "Connection Status",
    "description": "Description",
    "enhancement_plan": "Enhancement Plan",
    "protected_boundaries": "Protected App Boundaries",
    "recommended_path": "Recommended Enhancement",
    "candidate_overlays": "AI Workflow Opportunities",
    "candidate_adapters": "App Connections",
    "candidate_migrations": "Feature Opportunities",
    "human_decisions_required": "Decisions to Confirm",
    "not_in_scope": "Outside This Enhancement",
}

_ENHANCEMENT_PATHS = {
    "light_integration": {
        "label": "Add AI Workflows",
        "description": (
            "Enhance the existing application with AI assistants, intelligent "
            "workflows, automations, and conversational experiences while "
            "preserving the current application as the source of truth.\n\n"
            "Do not redesign or replace existing functionality."
        ),
    },
    "full_migration": {
        "label": "Build App Features",
        "description": (
            "Build new application features, pages, modules, workflows, and "
            "user experiences that integrate with the existing application.\n\n"
            "Only generate functionality included in the approved enhancement "
            "scope.\n\nDo not automatically rewrite the existing repository."
        ),
    },
}

_RECOMMENDED_ENHANCEMENT_LABELS = {
    "embed": "Embedded AI Experience",
    "bridge": "Connected AI Experience",
    "ecosystem": "Mozaiks-Powered App Capabilities",
    "gradual_modernization": "Feature Expansion in Stages",
    "light_integration": "Add AI Workflows",
    "full_migration": "Build App Features",
    "overlay": "AI Workflow Extension",
}

_OWNERSHIP_DISPLAY_LABELS = {
    "read_only_discovered": "Protected Existing App",
    "generated_overlay": "Mozaiks Extensions",
    "generated_owned": "Mozaiks Managed",
    "user_owned": "Customer Managed",
}


def _get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    try:
        if hasattr(context_variables, "get"):
            return context_variables.get(key, default)
        return context_variables[key]
    except (KeyError, TypeError):
        return default


def _display_label(mapping: dict[str, str], value: Any, default: str = "Unclassified") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    return mapping.get(text, text.replace("_", " ").replace("-", " ").title())


def _enhancement_path_label(build_path: Any) -> str:
    path = _ENHANCEMENT_PATHS.get(str(build_path))
    if path:
        return path["label"]
    return _display_label(_RECOMMENDED_ENHANCEMENT_LABELS, build_path, "Unselected")


def _format_enhancement_description(build_path: Any) -> str:
    path = _ENHANCEMENT_PATHS.get(str(build_path))
    if not path:
        return ""
    return f"{_SECTION_LABELS['description']}:\n{path['description']}"


def _format_adoption_plan(plan: dict[str, Any]) -> str:
    lines: list[str] = []
    path = plan.get("recommended_path") or plan.get("adoption_level") or ""
    if path:
        label = _display_label(_RECOMMENDED_ENHANCEMENT_LABELS, path)
        lines.append(f"  {_SECTION_LABELS['recommended_path']}: {label}")
    overlays = plan.get("candidate_overlays") or []
    if overlays:
        lines.append(
            f"  {_SECTION_LABELS['candidate_overlays']} ({len(overlays)}): "
            + ", ".join(str(o) for o in overlays[:8])
        )
        if len(overlays) > 8:
            lines.append(f"    ... and {len(overlays) - 8} more")
    adapters = plan.get("candidate_adapters") or []
    if adapters:
        lines.append(
            f"  {_SECTION_LABELS['candidate_adapters']}: "
            + ", ".join(str(a) for a in adapters[:5])
        )
    migrations = plan.get("candidate_migrations") or []
    if migrations:
        lines.append(
            f"  {_SECTION_LABELS['candidate_migrations']}: "
            + ", ".join(str(m) for m in migrations[:5])
        )
    decisions = plan.get("human_decisions_required") or []
    if decisions:
        lines.append(f"  {_SECTION_LABELS['human_decisions_required']}:")
        for decision in decisions[:4]:
            lines.append(f"    - {decision}")
    not_in_scope = plan.get("not_in_scope") or []
    if not_in_scope:
        lines.append(f"  {_SECTION_LABELS['not_in_scope']}:")
        for item in not_in_scope[:3]:
            lines.append(f"    - {item}")
    return "\n".join(lines)


def _format_ownership_summary(boundary_artifact: dict[str, Any]) -> str:
    boundaries = boundary_artifact.get("ownership_boundaries") or []
    if not boundaries:
        return "  No protected app boundaries declared."

    class_counts: dict[str, int] = {}
    protected_examples: list[str] = []
    extension_examples: list[str] = []

    for b in boundaries:
        cls = b.get("ownership") or b.get("ownership_class") or "unknown"
        label = _OWNERSHIP_DISPLAY_LABELS.get(str(cls), "Unclassified App Surface")
        class_counts[label] = class_counts.get(label, 0) + 1
        path = b.get("path_or_artifact") or ""
        if cls == "read_only_discovered" and len(protected_examples) < 4:
            protected_examples.append(path)
        elif cls == "generated_overlay" and len(extension_examples) < 4:
            extension_examples.append(path)

    lines: list[str] = []
    for label, count in sorted(class_counts.items()):
        lines.append(f"  {label}: {count} surface(s)")

    if protected_examples:
        lines.append(
            "  Protected existing app examples (do not modify without explicit approval): "
            + ", ".join(protected_examples)
        )
    if extension_examples:
        lines.append(
            "  Mozaiks extension targets (safe to generate): "
            + ", ".join(extension_examples)
        )
    return "\n".join(lines)


def inject_brownfield_adoption_context(
    agent_name: str | None = None,
    context_variables: Any = None,
    **_kwargs: Any,
) -> str:
    """Return an existing app enhancement prompt block for planning agents.

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
        f"{_SECTION_LABELS['enhancement']}: {_enhancement_path_label(build_path)}",
        f"{_SECTION_LABELS['connected_app']}: {app_id}",
    ]

    path_guidance = _format_enhancement_description(build_path)
    if path_guidance:
        sections.append("")
        sections.append(path_guidance)

    reg_status = registration.get("status") or ""
    if reg_status:
        sections.append("")
        sections.append(f"{_SECTION_LABELS['connection_status']}: {reg_status}")

    if adoption_plan:
        sections.append("")
        sections.append(f"{_SECTION_LABELS['enhancement_plan']}:")
        sections.append(_format_adoption_plan(adoption_plan))

    if ownership_boundary:
        sections.append("")
        sections.append(f"{_SECTION_LABELS['protected_boundaries']}:")
        sections.append(_format_ownership_summary(ownership_boundary))

    sections.append("")
    sections.extend(
        [
            "RULE:",
            "Preserve all protected existing application surfaces unless the user explicitly "
            "approves a staged modification.",
            "",
            'For the "Add AI Workflows" enhancement, generate only AI workflows, '
            "assistants, automations, and required application connections identified "
            "in the approved Enhancement Plan.",
            "",
            'For the "Build App Features" enhancement, generate only the approved '
            "application features and extensions without replacing existing functionality.",
        ]
    )

    return "\n".join(sections)
