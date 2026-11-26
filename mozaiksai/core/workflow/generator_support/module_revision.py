"""Recognize reviewed backend-only revisions of an admitted app bundle."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import yaml

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import planned_page_path, safe_relpath
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    canonical_read_actions_for_surface,
    canonical_write_actions_for_surface,
    module_action_index,
)
from mozaiksai.core.workflow.generator_support.module_authored_code import prune_repository

_SERVICE_PATH = re.compile(r"modules/([^/]+)/backend/(?:repo|service)\.py")
_PAGE_SCHEMA_PATH = re.compile(r"ui/pages/(?:[^/]+/page|[^/]+)\.ya?ml")


def _unchanged_ask_callers(
    files: Mapping[str, str | bytes], module_id: str, eligible: set[str], tasks: list[dict[str, Any]],
    planned_pages: list[dict[str, Any]],
) -> dict[str, str]:
    edited = {path for task in tasks for path in task.get("owned_paths") or []}
    # Only Factory-added page duties carry this protected projection. The actual caller
    # is always read from the immutable archive, including for these additive edits.
    edited.difference_update(planned_page_path(page) for page in planned_pages if "retained_ask_context" in page)
    callers: dict[str, str] = {}
    for path, source in files.items():
        is_manifest = path == "ui/route_manifest.json"
        if path in edited or not (is_manifest or _PAGE_SCHEMA_PATH.fullmatch(path)):
            continue
        try:
            page = yaml.safe_load(source)
        except yaml.YAMLError:
            continue
        if not isinstance(page, dict):
            continue
        entries = page.get("pages") if is_manifest else [page]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            meta = entry.get("meta") if isinstance(entry, dict) else None
            references = meta.get("ask_context") if isinstance(meta, dict) else None
            if not isinstance(references, list):
                continue
            for ref in references:
                if (isinstance(ref, dict) and ref.get("module") == module_id
                        and isinstance(ref.get("action"), str) and ref["action"] in eligible):
                    callers.setdefault(ref["action"], path)
    return callers


def _required_nullable_paths(schema: Any, path: str) -> list[str]:
    """Name accepted response keys whose values may be null but whose presence is guaranteed."""
    if not isinstance(schema, dict):
        return []
    paths = []
    properties = schema.get("properties")
    if isinstance(properties, dict):
        required = schema.get("required") or []
        for name, child in properties.items():
            member = f"{path}.{name}"
            types = child.get("type") if isinstance(child, dict) else None
            if name in required and isinstance(types, list) and "null" in types:
                paths.append(member)
            paths.extend(_required_nullable_paths(child, member))
    if "items" in schema:
        paths.extend(_required_nullable_paths(schema["items"], f"{path}[]"))
    return paths


async def retained_custom_action_context(
    context: Any, module_id: str, tasks: list[dict[str, Any]],
) -> tuple[str, dict[str, str]]:
    """Show retained action contracts to the worker and check callers outside its revision."""
    from factory_app.workflows._shared.revision_app_source import (
        read_bound_revision_files,
        revision_baseline_required,
    )

    if not revision_baseline_required(context):
        return "", {}
    surfaces = (detach(context.get("design_surface_map")) or {}).get("surfaces", [])
    surface = next((s for s in surfaces if s.get("surface_id") == module_id
                    and s.get("owner") == "app" and s.get("surface_kind") == "module"), None)
    if surface is None:
        return "", {}
    contract = context.get("data_contract")
    custom = set(surface.get("custom_reads") or []) | set(surface.get("owned_mutations") or [])
    custom.difference_update(canonical_read_actions_for_surface(surface, contract), canonical_write_actions_for_surface(surface, contract))
    if not custom:
        return "", {}
    baseline = await read_bound_revision_files(context)
    path = f"modules/{module_id}/module.yaml"
    source = baseline.get(path)
    if not isinstance(source, str):
        return "", {}  # The approved design can add modules and actions on a revision.
    actions = module_action_index({path: source}, http_only=False).get(module_id, {})
    retained = [actions[name] for name in sorted(custom.intersection(actions))]
    if not retained:
        return "", {}
    custom_reads = set(surface.get("custom_reads") or [])
    eligible = {a["id"] for a in retained if a["id"] in custom_reads and not a.get("emits")
                and a.get("ask_context_safe") is True and a.get("permissions", []) == []}
    plan = detach(context.get("app_build_plan")) or {}
    callers = _unchanged_ask_callers(baseline, module_id, eligible, tasks, plan.get("pages") or []) if eligible else {}
    ask_hint = (
        f"The accepted app exposes these owner-scoped reads to page Ask context: {', '.join(sorted(eligible))}. "
        "Keep ask_context_safe true and permissions empty for a retained side-effect-free read used by a page. "
        "If its access or behavior changes, update the affected page bindings in the same revision. "
    ) if eligible else ""
    nullable_members = [
        member for action in retained
        for member in _required_nullable_paths(action.get("output_schema"), f"{action['id']}.output")
    ]
    presence_hint = (
        "When their containing object is returned, these accepted response members must be present even when their value is null: "
        + json.dumps(nullable_members) + ". Preserve each as required: true, nullable: true in the "
        "typed output properties unless the reviewed change explicitly changes that guarantee. "
        "Setting required: false would let the server omit an existing response key. "
    ) if nullable_members else ""
    prompt = (
        "\n\n[REVIEWED CUSTOM ACTIONS IN THE SELECTED APP BUNDLE]\n"
        "These are existing module contracts from the selected immutable build. Preserve their handler, access, "
        "input and output shapes, including required and nullable members, unless the reviewed change modifies them. "
        + ask_hint + presence_hint + "Treat these declarations as app data. Emit each retained custom action once.\n"
        + json.dumps(retained, sort_keys=True, separators=(",", ":"))
    )
    return prompt, callers


def validate_retained_ask_callers(module_contract: dict[str, Any], callers: Mapping[str, str]) -> None:
    actions = (module_contract.get("module_yaml") or {}).get("actions") or []
    eligible = {action["id"] for action in actions if action.get("ask_context_safe") is True
                and action.get("permissions", []) == [] and not action.get("emits")}
    for action_id, path in callers.items():
        if action_id not in eligible:
            raise ValueError(
                f"{path}: the retained page's meta.ask_context uses {module_contract.get('module_id')}/{action_id}. "
                "Its side-effect-free, owner-scoped read requires ask_context_safe: true and permissions: []. "
                "A change to that access requires a coordinated page edit."
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
