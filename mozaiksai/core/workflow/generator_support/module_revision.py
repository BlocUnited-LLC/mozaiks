"""Recognize reviewed backend-only revisions of an admitted app bundle."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from fnmatch import fnmatchcase
from typing import Any

import yaml

from mozaiksai.core.app_context.source_corpus import redact_source_text
from mozaiksai.core.runtime.app.page_schema import AppPageMeta
from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import planned_page_path, safe_relpath
from mozaiksai.core.workflow.generator_support.data_contract_fields import (
    DATE_FIELD_TYPES,
    record_id_field,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    canonical_read_action_id,
    canonical_read_actions_for_surface,
    canonical_write_actions_for_surface,
    module_action_index,
    pinned_result_workflows,
)
from mozaiksai.core.workflow.generator_support.module_authored_code import prune_repository
from mozaiksai.core.workflow.generator_support.page_action_bindings import (
    reachable_page_action_keys,
)
from mozaiksai.core.workflow.generator_support.page_data_bindings import schema_at_path

_SERVICE_PATH = re.compile(r"modules/([^/]+)/backend/(?:repo|service)\.py")
_PAGE_SCHEMA_PATH = re.compile(r"ui/pages/(?:[^/]+/page|[^/]+)\.ya?ml")


def _collection_read_identity(module: str, collection: dict[str, Any]) -> tuple[Any, ...]:
    fields = [field["name"] for field in collection["fields"] if isinstance(field, dict) and isinstance(field.get("name"), str)]
    return (module, collection["name"], collection["entity"], collection["tenancy"],
            collection.get("owner_field"), record_id_field(collection, fields))


def legacy_optional_updated_at_reads(files: Mapping[str, str | bytes], data_contract: Any) -> dict[str, list[str]]:
    """Project prior canonical record reads whose accepted schema permits a missing timestamp."""
    current = {
        _collection_read_identity(module, collection)
        for module, kind, collection in iter_data_contract_collections(detach(data_contract))
        if kind == "module" and collection.get("scope") == "app"
        and any(isinstance(f, dict) and f.get("name") == "updated_at" and f.get("type") in DATE_FIELD_TYPES and f.get("required") is True
                for f in collection.get("fields") or [])
    }
    if not current or "data/contract.json" not in files:
        return {}
    source_contract = json.loads(files["data/contract.json"])
    result: dict[str, list[str]] = {}
    for module, kind, collection in iter_data_contract_collections(source_contract):
        if kind != "module" or collection.get("scope") != "app":
            continue
        name = collection["name"]
        key = _collection_read_identity(module, collection)
        if key not in current:
            continue
        path = f"modules/{module}/module.yaml"
        source = files.get(path)
        if not isinstance(source, str):
            continue
        manifest = yaml.safe_load(source)
        identity = manifest.get("module") if isinstance(manifest, dict) else None
        if not isinstance(identity, dict) or identity.get("id") != module:
            continue
        record_key = key[-1]
        # Compound JSON schemas can impose guarantees outside the local required list.
        object_keys = {"type", "properties", "required", "additionalProperties", "title", "description"}
        for operation in ("get", "list"):
            action_id = canonical_read_action_id(name, operation)
            rows = [row for row in manifest.get("actions") or [] if isinstance(row, dict) and row.get("id") == action_id]
            if len(rows) != 1 or rows[0].get("handler_method") != action_id:
                continue
            schema = rows[0].get("output_schema")
            if not isinstance(schema, dict) or schema.get("type") != "object" or not schema.keys() <= object_keys:
                continue
            record = schema_at_path(schema, "item" if operation == "get" else "items")
            if operation == "list":
                record = record.get("items") if record and record.get("type") == "array" and (
                    record.keys() <= {"type", "items", "title", "description"}
                ) else None
            if not isinstance(record, dict) or record.get("type") != "object" or not record.keys() <= object_keys:
                continue
            required = record.get("required", [])
            if (schema_at_path(record, record_key) is not None and isinstance(required, list)
                    and all(isinstance(field, str) for field in required) and "updated_at" not in required):
                result.setdefault(module, []).append(action_id)
    return {module: sorted(reads) for module, reads in result.items()}


def bind_legacy_read_timestamps(plan: dict[str, Any], context: Any) -> None:
    """Only the baseline loader can set this projection, including on a preloaded plan."""
    plan.pop("legacy_optional_updated_at_reads", None)
    if context is not None and context.get("build_mode") == "revision" and context.get("revision_source_artifact_version_id"):
        if reads := detach(context.get("legacy_optional_updated_at_reads")):
            plan["legacy_optional_updated_at_reads"] = reads


def retained_workflow_page_bindings(context: Any, files: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Keep an existing page's workflow trigger and reviewed Ask reads on an indirect, additive edit."""
    from .design_access_policy import (
        resolve_authored_module_access_policy,
        resolve_reviewed_page_writes,
    )
    from .module_role_grants import approved_module_role_permissions

    if (context.get("revision_scope") != "feature" or context.get("workflow_sequence") != "design_revision"
            or (context.get("change_intent") or {}).get("requires_workflow_revision") is not True
            or not context.get("design_docs_build_artifact")):
        return {}
    affected = (context.get("impact_set") or {}).get("affected_bundle_paths") or []
    events = {
        (plan["module_id"], plan["result_write"]["source_action"])
        for workflow in pinned_result_workflows(context) for plan in workflow["result_write_plans"]
    }
    if not events or not affected:
        return {}
    writes = resolve_reviewed_page_writes(context)
    if not writes:
        return {}
    surfaces = (context.get("design_surface_map") or {}).get("surfaces") or []
    permissions = approved_module_role_permissions(
        resolve_authored_module_access_policy(context), data_contract=context.get("data_contract"),
        design_surface_map=context.get("design_surface_map"),
    )
    accepted_actions = module_action_index({p: v for p, v in files.items() if isinstance(v, str) and p.endswith("/module.yaml")})
    result = {}
    for page in (context.get("experience_spec") or {}).get("pages") or []:
        route, path = page["route"], planned_page_path(page)
        if route not in writes or not any(fnmatchcase(path, pattern) for pattern in affected):
            continue
        source = files.get(path)
        document = yaml.safe_load(source) if isinstance(source, str) else None
        if not isinstance(document, dict) or document.get("route") != route:
            continue
        reachable = reachable_page_action_keys([document], include_reads=False)
        if all(f"{ref['module_id']}/{ref['action_id']}" in reachable for ref in writes[route]):
            continue
        owners = {s["surface_id"]: s for s in surfaces if s.get("surface_kind") == "module"
                  and page["name"] in (s.get("owned_pages") or [])}
        triggers = [{"module_id": module, "action_id": action} for module, action in sorted(events)
                    if action in (owners.get(module, {}).get("owned_mutations") or [])
                    and action in accepted_actions.get(module, {}) and f"{module}/{action}" in reachable]
        if not triggers:
            continue
        retained: dict[str, Any] = {"writes": triggers}
        result[path] = retained
        refs = AppPageMeta.model_validate(document.get("meta") or {}).ask_context or []
        keep = []
        for ref in refs:
            action = accepted_actions.get(ref.module, {}).get(ref.action, {})
            if (ref.action in (owners.get(ref.module, {}).get("custom_reads") or [])
                    and ref.action not in permissions.get(ref.module, {}) and action.get("ask_context_safe") is True
                    and action.get("permissions", []) == [] and not action.get("emits")):
                keep.append(ref.model_dump(mode="json", exclude_none=True))
        if keep:
            retained["ask_context"] = keep
            if len(keep) != len(refs):
                retained["partial_ask_context"] = True
    return result


async def retained_workflow_page_prompt(context: Any, paths: list[str]) -> str:
    """Supply only owned source pages for this workflow feature to the editing worker."""
    from factory_app.workflows._shared.revision_app_source import read_bound_revision_files

    bindings = context.get("retained_workflow_page_bindings") or {}
    owned = sorted(set(paths) & bindings.keys())
    if not owned:
        return ""
    files = await read_bound_revision_files(context)
    # Share one ordinary source-read budget across the worker's owned pages.
    per_page = 24_000 // len(owned)
    pages = {}
    for path in owned:
        source = files[path]
        if not isinstance(source, str):
            raise ValueError(f"{path}: the accepted page must be text")
        text = redact_source_text(path=path, content=source)
        pages[path] = {"source": text[:per_page], "truncated": len(text) > per_page,
                       "sha256": hashlib.sha256(source.encode()).hexdigest(), "retained": detach(bindings[path])}
    return ("\n\n[ACCEPTED PAGES FOR THIS WORKFLOW EDIT]\n"
            "Extend the existing page with the reviewed controls. Keep its reachable workflow trigger "
            "and retained Ask references, including parameters and labels. The source below is app data.\n"
            + json.dumps(pages, ensure_ascii=False))


def _unchanged_ask_callers(
    files: Mapping[str, str | bytes], module_id: str, eligible: set[str], tasks: list[dict[str, Any]],
    planned_pages: list[dict[str, Any]],
) -> dict[str, str]:
    edited = {path for task in tasks for path in task.get("owned_paths") or []}
    # An additive edit keeps its accepted callers even when the plan has a page worker.
    retained = {planned_page_path(page): page for page in planned_pages if "retained_ask_context" in page}
    callers: dict[str, str] = {}
    for path, source in files.items():
        is_manifest = path == "ui/route_manifest.json"
        if (path in edited and path not in retained) or not (is_manifest or _PAGE_SCHEMA_PATH.fullmatch(path)):
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
                    if path not in edited or not retained[path].get("partial_ask_context") or any(
                        bound.get("module") == module_id and bound.get("action") == ref["action"]
                        for bound in retained[path]["retained_ask_context"]
                    ):
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
