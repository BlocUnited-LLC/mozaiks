"""Close generated entity read contracts from declared page intent and ownership."""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import yaml

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    _materialize_schema_contract,
    _unwrap_output_envelope,
    extract_code_file_map_from_payload,
)

_LIST_PAGE_TYPES = {"record_list", "gallery", "workflow_board", "activity_feed", "split_view"}
_DETAIL_PAGE_TYPES = {"record_detail", "split_view"}
_LIST_PRIMITIVES = {"DataTable", "ResourceTable"}


def _property(name: str, kind: str, *, required: bool = False) -> dict[str, Any]:
    return {
        "name": name, "type": kind, "required": required,
        "description": None, "enum_values": None,
        "items_type": "object" if kind == "array" else None,
    }


def _schema(properties: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "description": None, "items_type": None}


def _read_action(entity: str, operation: str) -> dict[str, Any]:
    action_id = f"{operation}_{entity}"
    is_list = operation == "list"
    return {
        "id": action_id,
        "description": (
            f"List {entity} within the requesting principal's declared data scope. "
            "Return items and total; apply page and page_size after counting scoped records."
            if is_list else
            f"Get one {entity} record by id within the requesting principal's declared data scope."
        ),
        "handler_method": action_id,
        "api_surface": None,
        "input_schema": _schema(
            [_property("page", "integer"), _property("page_size", "integer"), _property("search", "string")]
            if is_list else [_property("id", "string", required=True)]
        ),
        "output_schema": _schema(
            [_property("items", "array", required=True), _property("total", "integer", required=True)]
            if is_list else [_property("item", "object", required=True)]
        ),
        "permissions": [],
        "emits": [],
        "entitlement_gate": None,
        "ask_context_safe": False,
    }


def _section_intents(node: Any) -> Iterator[tuple[str, dict[str, Any] | None]]:
    """Read primitive choices throughout typed hints and their nested config scaffolds."""
    if isinstance(node, list):
        for item in node:
            yield from _section_intents(item)
    elif isinstance(node, dict):
        primitive = node.get("primitive")
        if isinstance(primitive, str):
            config = node.get("config")
            source = node.get("data_source")
            if source is None and isinstance(config, dict):
                source = config.get("data_source")
            yield primitive, source if isinstance(source, dict) else None
        for key, value in node.items():
            if key == "config_hint" and isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ValueError("Section config_hint must contain valid JSON before read action closure") from exc
            yield from _section_intents(value)


def _required_reads(
    module_id: str, plan: dict[str, Any], data_contract: dict[str, Any] | None,
    existing_action_ids: set[str],
) -> set[tuple[str, str]]:
    packs = [
        pack for pack in plan.get("capability_packs") or []
        if isinstance(pack, dict) and pack.get("capability_pack_id") == module_id
        and pack.get("capability_source") == "generated_module"
    ]
    if not packs:
        return set()
    if len(packs) != 1:
        raise ValueError(f"{module_id}: read action closure requires one owning capability pack")
    pack_entities = set(packs[0].get("primary_entities") or [])
    pages = [page for page in plan.get("pages") or [] if isinstance(page, dict)]
    relevant_pages = []
    for page in pages:
        sections = list(_section_intents(page.get("sections_hint") or []))
        sources = [source for _, source in sections if source is not None]
        operations = set()
        if (not sources and page.get("page_type_hint") in _LIST_PAGE_TYPES) or any(
            primitive in _LIST_PRIMITIVES and source is None for primitive, source in sections
        ):
            operations.add("list")
        if not sources and page.get("page_type_hint") in _DETAIL_PAGE_TYPES:
            operations.add("get")
        if (operations and set(page.get("primary_entities") or []) & pack_entities
                or any(source.get("module_id") == module_id and source.get("action_id") not in existing_action_ids
                       for source in sources)):
            relevant_pages.append((page, operations, sources))
    if not relevant_pages:
        return set()

    surfaces = [
        surface for surface in (data_contract or {}).get("surfaces") or []
        if isinstance(surface, dict) and surface.get("surface_id") == module_id
        and surface.get("surface_kind") == "module"
    ]
    if len(surfaces) != 1 or not surfaces[0].get("collections"):
        if not any(operations for _, operations, _ in relevant_pages):
            # Arbitrary explicit action references are not persistence declarations.
            # Unknown references remain errors in the closed page action inventory.
            return set()
        raise ValueError(
            f"{module_id}: page data sources require data_contract.surfaces declaring "
            "the module's owned collections before read actions can be constructed"
        )
    collections: list[str] = []
    for collection in surfaces[0]["collections"]:
        name = collection.get("name")
        ownership = collection.get("ownership") or {}
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                or ownership.get("surface_id") != module_id
                or ownership.get("surface_kind") != "module"):
            raise ValueError(f"{module_id}: read action closure requires canonical collection name and module ownership")
        if name in collections:
            raise ValueError(f"{module_id}: duplicate owned collection {name!r}")
        collections.append(name)

    reads: set[tuple[str, str]] = set()
    for page, operations, sources in relevant_pages:
        # An explicit section source closes only the exact canonical action it names.
        for source in sources:
            if source.get("module_id") == module_id:
                for entity in collections:
                    for operation in ("list", "get"):
                        if source.get("action_id") == f"{operation}_{entity}":
                            reads.add((entity, operation))
        if not operations:
            continue
        for entity in set(page.get("primary_entities") or []) & pack_entities:
            if entity in collections:
                name = entity
            elif len(collections) == 1 and len(pack_entities) == 1:
                # The approved pack owns one entity and its contract owns one collection.
                name = collections[0]
            else:
                raise ValueError(
                    f"{module_id}: page entity {entity!r} must name an owned collection "
                    "or sections_hint.data_source must select its exact read action"
                )
            reads.update((name, operation) for operation in operations)
    return reads


def close_module_read_actions(
    payload: Any, *, app_build_plan: Any, data_contract: Any = None,
) -> Any:
    """Return detached typed output with every page-required entity read declared.

    The task runner uses this before publishing dependency outputs, so page and
    backend workers receive the same expanded module contract.
    """
    output = _unwrap_output_envelope(detach(payload))
    plan = detach(app_build_plan)
    if not isinstance(output, dict) or not isinstance(plan, dict):
        return output
    bundle = output.get("module_contract")
    if not isinstance(bundle, dict):
        files = extract_code_file_map_from_payload(output)
        changes = materialize_module_read_actions(files, app_build_plan=plan, data_contract=data_contract)
        if changes:
            files.update(changes)
            output["code_files"] = [{"filename": path, "content": content} for path, content in sorted(files.items())]
        return output
    if not isinstance(bundle.get("module_yaml"), dict):
        return output
    manifest = bundle["module_yaml"]
    module_id = str(bundle.get("module_id") or "")
    if manifest.get("module", {}).get("id") != module_id:
        raise ValueError("Read action closure requires matching module_contract and module.yaml identities")
    contract = detach(data_contract) if data_contract is not None else plan.get("data_contract")
    if contract is not None and not isinstance(contract, dict):
        raise ValueError("Read action closure requires a structured data_contract")
    actions = manifest.setdefault("actions", [])
    existing = {action["id"] for action in actions if action.get("id")}
    required = _required_reads(module_id, plan, contract, existing)
    if not required:
        return output
    for entity, operation in sorted(required):
        action = _read_action(entity, operation)
        if action["id"] not in existing:
            if any(
                declared.get("permissions") or declared.get("entitlement_gate")
                or declared.get("api_surface") in {"internal", "admin_internal"}
                for declared in actions
            ):
                raise ValueError(
                    f"{module_id}: explicitly declare {action['id']!r} and its access policy; "
                    "read action construction cannot infer permissions, entitlement gates, "
                    "or HTTP exposure from protected actions"
                )
            actions.append(action)
            existing.add(action["id"])
    return output


def materialize_module_read_actions(
    files_map: dict[str, str], *, app_build_plan: Any, data_contract: Any = None,
) -> dict[str, str]:
    """Render closed module manifests when assembling an admitted app bundle."""
    changed: dict[str, str] = {}
    if app_build_plan is None:
        return changed
    for path, content in files_map.items():
        match = re.fullmatch(r"modules/([^/]+)/module\.yaml", path)
        if not match:
            continue
        manifest = yaml.safe_load(content)
        if not isinstance(manifest, dict):
            raise ValueError(f"{path}: module manifest must be an object")
        payload = {"module_contract": {"module_id": match[1], "module_yaml": manifest}}
        closed = close_module_read_actions(payload, app_build_plan=app_build_plan, data_contract=data_contract)
        expanded = closed["module_contract"]["module_yaml"]
        if expanded == manifest:
            continue
        for action in expanded.get("actions") or []:
            for key in ("input_schema", "output_schema"):
                if isinstance(action.get(key), dict):
                    action[key] = _materialize_schema_contract(action[key], closed_request=key == "input_schema")
        changed[path] = yaml.safe_dump(expanded, sort_keys=False, allow_unicode=True)
    return changed
