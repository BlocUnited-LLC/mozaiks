"""Planned page hints bind only to actions that exist."""

from __future__ import annotations

from typing import Any

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _managed_facade_route_rules,
    _normalized_owned_paths,
)
from mozaiksai.core.workflow.generator_support.code_files import planned_page_path
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    all_module_actions,
    pack_owned_output_paths,
)


def drop_unapproved_page_data_sources(plan: dict[str, Any], context: Any) -> list[str]:
    """Remove a section hint's data_source that names no approved action.

    The live run at 0b1b740d (chat 645653f2) planned its Dashboard KPI strip on
    ``tasks/get_kpi_stats``. The approved design declared create/update/delete
    for tasks and code builds list/get; nothing declares get_kpi_stats, so page
    compilation can never bind it. Review accepted the hint, and the page task
    failed both attempts on "references unknown module/action".

    A hint naming a nonexistent action carries no decision code cannot make:
    pages bind only to the approved inventory, the same one page compilation
    enforces. So the data_source is dropped, the section keeps its primitive,
    intent and title, and the page_bundle task that owns the page is told which
    binding was removed and which actions exist. No action is constructed.

    Provider pairs that facade routing rebinds are checked at their facade, and
    pages a selected pack writes keep the bindings its template fixes. Without
    an approved design the inventory is open, so nothing is dropped.
    """
    if context is None or not context.get("design_surface_map"):
        return []
    inventory = all_module_actions(context)
    routes = _managed_facade_route_rules(plan.get("capability_packs") or [], context_variables=context)
    pack_paths = pack_owned_output_paths(context)
    repairs: list[str] = []
    removed: dict[str, list[str]] = {}
    for page in plan.get("pages") or []:
        path = planned_page_path(page)
        if path in pack_paths:
            continue
        for index, hint in enumerate(page.get("sections_hint") or []):
            source = hint.get("data_source")
            if not isinstance(source, dict):
                continue
            module_id, action_id = str(source.get("module_id") or ""), str(source.get("action_id") or "")
            target = routes.get((module_id, action_id), module_id)
            if action_id in inventory.get(target, ()):
                continue
            hint["data_source"] = None
            section_id = hint.get("section_id_hint") or f"sections_hint[{index}]"
            section = f"{page.get('name')} section {section_id!r}"
            valid = (
                f"{target} declares {', '.join(inventory[target]) or 'no actions'}" if target in inventory
                else f"approved modules are {', '.join(sorted(inventory)) or 'none'}"
            )
            removed.setdefault(path, []).append(f"{section} named {module_id}/{action_id} ({valid})")
            repairs.append(f"{section}: dropped data_source '{module_id}/{action_id}', not an approved action")
    if not removed:
        return repairs
    for task in plan.get("build_tasks") or []:
        if task.get("task_type") != "page_bundle":
            continue
        lines = [line for path in _normalized_owned_paths(task) for line in removed.get(path, [])]
        if not lines:
            continue
        note = (
            "Planned data sources removed because no approved action exists: "
            + "; ".join(lines)
            + ". Bind each such section only to an action its module declares, through that "
            "action's declared output keys; do not invent actions."
        )
        message = str(task.get("initial_message") or "").rstrip()
        task["initial_message"] = "\n\n".join(part for part in (message, note) if part)
        repairs.append(f"{task.get('task_id')}: noted {len(lines)} removed page data source(s)")
    return repairs
