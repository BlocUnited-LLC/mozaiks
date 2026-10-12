from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class UISurfaceContract:
    surface_id: str
    owner: str
    artifact_family: str
    writes: tuple[str, ...]
    use_when: str
    avoid_when: str


_UI_SURFACE_CONTRACTS: tuple[UISurfaceContract, ...] = (
    UISurfaceContract(
        surface_id="declarative_page",
        owner="AppSchemaAgent",
        artifact_family="App UI",
        writes=("ui/pages/*.yaml",),
        use_when=(
            "Shipped page primitives and supported shell configuration faithfully express "
            "the approved composition, visual hierarchy, responsive behavior, and interaction."
        ),
        avoid_when=(
            "Primitive composition would discard an approved experience requirement, "
            "including bespoke interaction, visual hierarchy, or responsive composition."
        ),
    ),
    UISurfaceContract(
        surface_id="custom_react_page",
        owner="AppSchemaAgent",
        artifact_family="App UI escape hatch",
        writes=("ui/route_manifest.json", "ui/pages/custom/*.jsx", "ui/index.js"),
        use_when=(
            "A persistent full-page route has a real primitive gap: declarative composition "
            "cannot faithfully express the approved experience. The gap may be "
            "composition, visual hierarchy, responsive behavior, or interaction."
        ),
        avoid_when=(
            "The approved experience is faithfully covered by shipped composition, or "
            "only brand token values need to change. A page category alone neither "
            "requires nor forbids custom React."
        ),
    ),
    UISurfaceContract(
        surface_id="agent_tool",
        owner="AgentGenerator UIFileGenerator",
        artifact_family="Agent UI tools",
        writes=("workflows/{workflow}/ui/{WorkflowName}/*.jsx", "workflows/{workflow}/tools/*.py"),
        use_when=(
            "A live workflow checkpoint needs structured interaction inside chat, "
            "such as approval, choice, form input, artifact review, or table review."
        ),
        avoid_when=(
            "The user can answer in plain language through the composer, or the "
            "surface is a persistent app route."
        ),
    ),
    UISurfaceContract(
        surface_id="transition",
        owner="AgentGenerator PackMetadataAgent/UIFileGenerator",
        artifact_family="Transition UI",
        writes=(
            "extended_orchestration/extension_registry.json",
            "extended_orchestration/ui/transitions/*.js",
        ),
        use_when=(
            "A workflow sequence needs a shell-routed choice or confirm step before "
            "entering the next workflow stage."
        ),
        avoid_when=(
            "The interaction is a persistent app page, a workflow-local tool UI, or "
            "a simple route/action that shell config can express directly."
        ),
    ),
)


def get_ui_surface_contracts() -> tuple[UISurfaceContract, ...]:
    return _UI_SURFACE_CONTRACTS


def get_ui_surface_ids() -> tuple[str, ...]:
    return tuple(entry.surface_id for entry in _UI_SURFACE_CONTRACTS)


def validate_ui_surface_ids(
    values: Iterable[str] | None,
    *,
    context: str,
) -> list[str]:
    allowed = set(get_ui_surface_ids())
    normalized: list[str] = []
    invalid: list[str] = []

    if values is None:
        return normalized

    for value in values:
        if not isinstance(value, str):
            invalid.append(repr(value))
            continue
        surface_id = value.strip()
        if not surface_id:
            continue
        if surface_id not in allowed:
            invalid.append(surface_id)
            continue
        if surface_id not in normalized:
            normalized.append(surface_id)

    if invalid:
        raise ValueError(
            f"{context} references unsupported UI surfaces: {', '.join(invalid)}. "
            f"Allowed surfaces: {', '.join(get_ui_surface_ids())}"
        )

    return normalized


def format_ui_surface_taxonomy_guidance() -> str:
    lines: list[str] = ["Canonical UI surface taxonomy:"]

    for entry in _UI_SURFACE_CONTRACTS:
        writes = ", ".join(f"`{path}`" for path in entry.writes)
        lines.append("")
        lines.append(f"- `{entry.surface_id}` — owner={entry.owner}; family={entry.artifact_family}")
        lines.append(f"  Writes: {writes}")
        lines.append(f"  Use when: {entry.use_when}")
        lines.append(f"  Avoid when: {entry.avoid_when}")

    lines.append("")
    lines.append("Decision rules:")
    lines.append("- Use `declarative_page` by default when it faithfully expresses the approved experience.")
    lines.append("- Use `custom_react_page` for a real primitive gap, not as an unregistered rendering lane.")
    lines.append("- Use `agent_tool` only for live workflow interactions inside chat.")
    lines.append("- Use `transition` only for workflow-sequence routing/choice screens.")
    lines.append("- Do not move shell chrome, profile menus, notifications, or global nav into generated pages.")
    lines.append("- Do not create placeholder dashboards just to fill a surface.")
    lines.extend([
        "",
        "Experience fidelity:",
        "- Shared runtime contracts do not require a shared product layout. "
        "The builder UI is not the required appearance of a generated app.",
        "- A real primitive gap includes loss of approved composition, visual hierarchy, "
        "responsive behavior, or interaction; it is not limited to missing backend functionality. "
        "Narrowing a surface is valid only when all approved experience requirements survive.",
        "- Reuse public shared controls and the active app's semantic theme tokens without "
        "forcing every app into a sidebar, rounded cards, or a dashboard. "
        "Do not replace a distinctive approved experience with a stock layout or a palette swap.",
        "- Carry the design direction in the existing ExperienceSpec and frontend design document. "
        "Preserve page intent/design_intent and task acceptance_criteria through planning and refinement; "
        "use supplied approved theme and design context instead of repeating settled questions.",
        "- Keep the approved ui_surface and exact route/file ownership during implementation. "
        "A newly discovered gap requires repair of the owning design/plan before changing realization; "
        "never silently convert a custom page into a declarative placeholder.",
        "- Use supported shell modes and extension contracts. A missing shell capability is a "
        "reported framework gap, not permission to hide shell controls or create a second router.",
        "- Custom JSX is built, validated, and registered before mounting. "
        "It does not bypass module authorization, context-write rules, or workflow response contracts.",
    ])

    return "\n".join(lines)


__all__ = [
    "UISurfaceContract",
    "format_ui_surface_taxonomy_guidance",
    "get_ui_surface_contracts",
    "get_ui_surface_ids",
    "validate_ui_surface_ids",
]
