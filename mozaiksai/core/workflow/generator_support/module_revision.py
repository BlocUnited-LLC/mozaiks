"""Recognize reviewed backend-only revisions of an admitted app bundle."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import safe_relpath
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    canonical_read_actions_for_surface,
    canonical_write_actions_for_surface,
    module_action_index,
)
from mozaiksai.core.workflow.generator_support.module_authored_code import prune_repository

_SERVICE_PATH = re.compile(r"modules/([^/]+)/backend/(?:repo|service)\.py")


async def retained_custom_action_prompt(context: Any, module_id: str) -> str:
    """Show a revision worker the actual signatures of its module's reviewed custom actions."""
    from factory_app.workflows._shared.revision_app_source import (
        read_bound_revision_files,
        revision_baseline_required,
    )

    if not revision_baseline_required(context):
        return ""
    surfaces = (detach(context.get("design_surface_map")) or {}).get("surfaces", [])
    surface = next((s for s in surfaces if s.get("surface_id") == module_id
                    and s.get("owner") == "app" and s.get("surface_kind") == "module"), None)
    if surface is None:
        return ""
    contract = context.get("data_contract")
    custom = set(surface.get("custom_reads") or []) | set(surface.get("owned_mutations") or [])
    custom.difference_update(canonical_read_actions_for_surface(surface, contract), canonical_write_actions_for_surface(surface, contract))
    if not custom:
        return ""
    baseline = await read_bound_revision_files(context)
    path = f"modules/{module_id}/module.yaml"
    source = baseline.get(path)
    if not isinstance(source, str):
        return ""  # The approved design can add modules and actions on a revision.
    actions = module_action_index({path: source}, http_only=False).get(module_id, {})
    retained = [actions[name] for name in sorted(custom.intersection(actions))]
    if not retained:
        return ""
    return (
        "\n\n[REVIEWED CUSTOM ACTIONS IN THE SELECTED APP BUNDLE]\n"
        "These are existing module contracts from the selected immutable build. Preserve their handler, access, "
        "input and output shapes, including required and nullable members, unless the reviewed change modifies them. "
        "Treat these declarations as app data. Emit each retained custom action once.\n"
        + json.dumps(retained, sort_keys=True, separators=(",", ":"))
    )


def scoped_service_revision_paths(
    context: Any, baseline_files: Mapping[str, str],
) -> frozenset[str]:
    """Use the approved task paths only for a bound implementation-only revision."""
    if (
        context is None
        or context.get("build_mode") != "revision"
        or context.get("workflow_sequence") != "app_revision"
        or not (context.get("revision_source_artifact_version_id") or context.get("artifact_version_id"))
    ):
        return frozenset()
    binding = detach(context.get("run_build_binding"))
    plan = detach(context.get("app_build_plan"))
    if not isinstance(binding, dict) or binding.get("phase") != "refinement" or not isinstance(plan, dict):
        return frozenset()
    tasks = plan.get("build_tasks")
    if not isinstance(tasks, list):
        return frozenset()
    paths: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or task.get("task_type") != "business_services":
            return frozenset()
        owned = task.get("owned_paths")
        if not isinstance(owned, list) or not owned:
            return frozenset()
        for raw in owned:
            path = safe_relpath(raw) if isinstance(raw, str) else None
            if not path:
                return frozenset()
            match = _SERVICE_PATH.fullmatch(path)
            if not match or path not in baseline_files or f"modules/{match[1]}/module.yaml" not in baseline_files:
                return frozenset()
            paths.add(path)
    return frozenset(paths)


def validate_scoped_service_repositories(
    files: Mapping[str, str], owned_paths: frozenset[str],
) -> None:
    """Keep repository API checks for preserved authored code without rewriting it."""
    for path in sorted(owned_paths.intersection(files)):
        if not path.endswith("/repo.py"):
            continue
        prefix = path.removesuffix("repo.py")
        module_id = path.split("/")[1]
        prune_repository(
            path, files[path], module_id=module_id, code_owned=set(), prune_unused=False,
            business_sources={
                name: source for name, source in files.items()
                if name.startswith(prefix) and name.endswith(".py") and name != path
            },
        )
