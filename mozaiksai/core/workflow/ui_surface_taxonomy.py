from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple


@dataclass(frozen=True)
class UISurfaceContract:
    surface_id: str
    owner: str
    artifact_family: str
    writes: Tuple[str, ...]
    use_when: str
    avoid_when: str


_UI_SURFACE_CONTRACTS: Tuple[UISurfaceContract, ...] = (
    UISurfaceContract(
        surface_id="declarative_page",
        owner="AppSchemaAgent",
        artifact_family="App UI",
        writes=("ui/pages/*.yaml",),
        use_when=(
            "A persistent app page can be represented with shipped page primitives "
            "such as PageHeader, ResourceTable, Form, Empty, SummaryStrip, or simple composed sections."
        ),
        avoid_when=(
            "The route needs bespoke interaction choreography, canvas-like editing, "
            "or a layout that cannot be represented by primitive composition."
        ),
    ),
    UISurfaceContract(
        surface_id="custom_react_page",
        owner="AppSchemaAgent",
        artifact_family="App UI escape hatch",
        writes=("ui/route_manifest.json", "ui/pages/custom/*.jsx", "ui/index.js"),
        use_when=(
            "A persistent full-page route has a real primitive gap after attempting "
            "declarative composition."
        ),
        avoid_when=(
            "The page is a resource list, form, detail view, settings view, empty state, "
            "or dashboard-like page that can be simplified into shipped primitives."
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


def get_ui_surface_contracts() -> Tuple[UISurfaceContract, ...]:
    return _UI_SURFACE_CONTRACTS


def get_ui_surface_ids() -> Tuple[str, ...]:
    return tuple(entry.surface_id for entry in _UI_SURFACE_CONTRACTS)


def validate_ui_surface_ids(
    values: Iterable[str] | None,
    *,
    context: str,
) -> List[str]:
    allowed = set(get_ui_surface_ids())
    normalized: List[str] = []
    invalid: List[str] = []

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
    lines: List[str] = ["Canonical UI surface taxonomy:"]

    for entry in _UI_SURFACE_CONTRACTS:
        writes = ", ".join(f"`{path}`" for path in entry.writes)
        lines.append("")
        lines.append(f"- `{entry.surface_id}` — owner={entry.owner}; family={entry.artifact_family}")
        lines.append(f"  Writes: {writes}")
        lines.append(f"  Use when: {entry.use_when}")
        lines.append(f"  Avoid when: {entry.avoid_when}")

    lines.append("")
    lines.append("Decision rules:")
    lines.append("- Use `declarative_page` by default for persistent app routes.")
    lines.append("- Use `custom_react_page` only after a real primitive gap remains.")
    lines.append("- Use `agent_tool` only for live workflow interactions inside chat.")
    lines.append("- Use `transition` only for workflow-sequence routing/choice screens.")
    lines.append("- Do not move shell chrome, profile menus, notifications, or global nav into generated pages.")
    lines.append("- Do not create placeholder dashboards just to fill a surface.")

    return "\n".join(lines)


__all__ = [
    "UISurfaceContract",
    "format_ui_surface_taxonomy_guidance",
    "get_ui_surface_contracts",
    "get_ui_surface_ids",
    "validate_ui_surface_ids",
]
