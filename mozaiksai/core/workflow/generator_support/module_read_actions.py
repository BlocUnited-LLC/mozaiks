"""Construct secure canonical reads from approved entity ownership declarations."""
from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from typing import Any

import yaml

from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    _materialize_schema_contract,
    _unwrap_output_envelope,
    extract_code_file_map_from_payload,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    canonical_read_action_id,
)
from mozaiksai.core.workflow.generator_support.module_policy import render_module_policy


def _property(name: str, kind: str, *, required: bool = False) -> dict[str, Any]:
    return {
        "name": name, "type": kind, "required": required,
        "description": None, "enum_values": None,
        "items_type": "object" if kind == "array" else None,
    }


def _schema(properties: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "description": None, "items_type": None}


def _record_response_schema(collection: dict[str, Any]) -> dict[str, Any]:
    """Declare exactly the fields projected by the canonical read service."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in collection.get("fields") or []:
        name = field["name"]
        kind = "string" if name == "_id" else field.get("type")
        declaration: dict[str, Any] = {}
        if kind in {"string", "number", "integer", "boolean", "object", "array", "null"}:
            declaration["type"] = [kind, "null"] if field.get("nullable") and kind != "null" else kind
        # Logical storage types outside JSON Schema keep their declared names;
        # the page compiler needs field identity, not an invented wire encoding.
        if field.get("enum"):
            declaration["enum"] = [*field["enum"], *([None] if field.get("nullable") else [])]
        properties[name] = declaration
        if field.get("required"):
            required.append(name)
    result: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def _read_action(collection: dict[str, Any], operation: str) -> dict[str, Any]:
    collection_name = collection["name"]
    action_id = canonical_read_action_id(collection_name, operation)
    is_list = operation == "list"
    record = _record_response_schema(collection)
    return {
        "id": action_id,
        "description": (
            f"List {collection_name} within the requesting principal's declared data scope. "
            "Return items and total; apply page and page_size after counting scoped records."
            if is_list else
            f"Get one {collection_name} record by id within the requesting principal's declared data scope."
        ),
        "handler_method": action_id,
        "api_surface": None,
        "input_schema": _schema(
            [_property("page", "integer"), _property("page_size", "integer"), _property("search", "string")]
            if is_list else [_property("id", "string", required=True)]
        ),
        "output_schema": {
            "type": "object",
            "properties": {"items": {"type": "array", "items": record}, "total": {"type": "integer"}}
            if is_list else {"item": record},
            "required": ["items", "total"] if is_list else ["item"],
        },
        "permissions": [],
        "emits": [],
        "entitlement_gate": None,
        "ask_context_safe": False,
    }


def _owned_collections(module_id: str, plan: dict[str, Any], data_contract: Any) -> list[dict[str, Any]]:
    packs = [
        pack for pack in plan.get("capability_packs") or []
        if isinstance(pack, dict) and pack.get("capability_pack_id") == module_id
        and pack.get("capability_source") == "generated_module"
    ]
    if not packs:
        return []
    if len(packs) != 1:
        raise ValueError(f"{module_id}: read action closure requires one owning capability pack")
    collections = [
        collection for owner, kind, collection in iter_data_contract_collections(data_contract or {})
        if owner == module_id and kind == "module"
    ]
    if not collections:
        return []  # Nonpersistent modules and managed facades have no canonical entity reads.
    # Validate owner fields and tenancy using the same compiler that renders policy.py.
    render_module_policy(module_id, collections)
    approved_entities = set(packs[0].get("primary_entities") or [])
    for collection in collections:
        canonical_read_action_id(collection.get("name") or "", "list")
        entity = collection.get("entity")
        if not isinstance(entity, str) or entity not in approved_entities:
            raise ValueError(
                f"{module_id}: collection {collection.get('name')!r} must declare entity as one of "
                f"primary_entities: {sorted(approved_entities)!r}; collection names are not entity mappings"
            )
    return list(collections)


def _protected_writes(
    actions: list[dict[str, Any]], canonical_ids: set[str], *, module_id: str, subscription_contract: Any,
) -> bool:
    approved = detach(subscription_contract) or {}
    if any(
        update.get("module_id") == module_id and update.get("action_id") not in canonical_ids
        and update.get("entitlement_gate")
        for update in approved.get("module_contract_updates") or []
    ):
        return True
    return any(
        action.get("id") not in canonical_ids
        and (action.get("permissions") or action.get("entitlement_gate")
             or action.get("api_surface") in {"internal", "admin_internal"})
        for action in actions
    )


def _authored_app_wide_access(action: dict[str, Any]) -> bool:
    """Keep explicit app-wide access choices outside canonical read construction."""
    return bool(
        action.get("permissions") or action.get("api_surface") is not None
        or action.get("handler_method") != action.get("id")
    )


def _close_manifest(
    module_id: str, manifest: dict[str, Any], plan: dict[str, Any], contract: Any, subscription_contract: Any,
) -> None:
    collections = _owned_collections(module_id, plan, contract)
    if not collections:
        return
    actions = manifest.setdefault("actions", [])
    existing = {action["id"]: action for action in actions if action.get("id")}
    canonical_ids = {
        canonical_read_action_id(collection["name"], operation)
        for collection in collections for operation in ("get", "list")
    }
    protected = _protected_writes(
        actions, canonical_ids, module_id=module_id, subscription_contract=subscription_contract,
    )
    for collection in collections:
        for operation in ("get", "list"):
            action = _read_action(collection, operation)
            prior = existing.get(action["id"])
            if collection["tenancy"] == "app_wide" and protected:
                if prior is None or not {
                    "handler_method", "input_schema", "output_schema", "permissions", "api_surface",
                }.issubset(prior):
                    raise ValueError(
                        f"{module_id}: app_wide collection {collection['name']!r} has protected writes; "
                        f"explicitly declare {action['id']!r} in module.yaml.actions with handler_method, "
                        "input_schema, output_schema, permissions (use [] only for deliberate unrestricted "
                        "role access), and api_surface (null, public, public_readonly, internal, or admin_internal). "
                        "Canonical reads remain ungated; declare paid behavior as a separate custom read."
                    )
                continue
            if collection["tenancy"] == "app_wide" and prior is not None and _authored_app_wide_access(prior):
                continue
            # Canonical owner-scoped reads never inherit roles from sibling writes.
            # Preserve authored gates so the gate compiler can reject forbidden read gates.
            if prior is not None:
                action["entitlement_gate"] = prior.get("entitlement_gate")
                prior.clear()
                prior.update(action)
            else:
                actions.append(action)
                existing[action["id"]] = action


def close_module_read_actions(
    payload: Any, *, app_build_plan: Any, data_contract: Any = None, design_surface_map: Any = None,
    subscription_contract: Any = None,
) -> Any:
    """Return detached output with every owned entity's canonical list/get declared."""
    output = _unwrap_output_envelope(detach(payload))
    plan = detach(app_build_plan)
    if not isinstance(output, dict) or not isinstance(plan, dict):
        return output
    contract = detach(data_contract)
    if contract is not None and not isinstance(contract, dict):
        raise ValueError("Read action closure requires a structured data_contract")
    bundle = output.get("module_contract")
    if not isinstance(bundle, dict):
        files = extract_code_file_map_from_payload(output)
        changes = materialize_module_read_actions(
            files, app_build_plan=plan, data_contract=contract, design_surface_map=design_surface_map,
            subscription_contract=subscription_contract,
        )
        if changes:
            files.update(changes)
            output["code_files"] = [{"filename": path, "content": content} for path, content in sorted(files.items())]
        return output
    manifest = bundle.get("module_yaml")
    if not isinstance(manifest, dict):
        return output
    module_id = str(bundle.get("module_id") or "")
    if manifest.get("module", {}).get("id") != module_id:
        raise ValueError("Read action closure requires matching module_contract and module.yaml identities")
    _close_manifest(module_id, manifest, plan, contract, subscription_contract)
    surface_map = detach(design_surface_map) or {}
    approved = {
        action for surface in surface_map.get("surfaces") or []
        if surface.get("surface_id") == module_id and surface.get("surface_kind") == "module"
        for action in [*(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or [])]
    }
    missing = approved - {action.get("id") for action in manifest.get("actions") or []}
    if missing:
        raise ValueError(
            f"{module_id}: declare approved actions {sorted(missing)!r} in module.yaml.actions with their "
            "access policies; ServiceAgent must implement declared custom reads and writes"
        )
    return output


def materialize_module_read_actions(
    files_map: Mapping[str, str], *, app_build_plan: Any, data_contract: Any = None,
    design_surface_map: Any = None,
    subscription_contract: Any = None,
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
        closed = close_module_read_actions(
            payload, app_build_plan=app_build_plan, data_contract=data_contract,
            design_surface_map=design_surface_map,
            subscription_contract=subscription_contract,
        )
        expanded = closed["module_contract"]["module_yaml"]
        for action in expanded.get("actions") or []:
            for key in ("input_schema", "output_schema"):
                if isinstance(action.get(key), dict):
                    action[key] = _materialize_schema_contract(action[key], closed_request=key == "input_schema")
        if expanded != manifest:
            changed[path] = yaml.safe_dump(expanded, sort_keys=False, allow_unicode=True)
    return changed


def _replace_functions(source: str, functions: dict[str, str], *, class_name: str | None = None) -> str:
    """Replace only compiler-owned functions, preserving authored code and comments."""
    tree = ast.parse(source)
    body = tree.body
    class_node = None
    if class_name is not None:
        class_node = next((node for node in body if isinstance(node, ast.ClassDef) and node.name == class_name), None)
        if class_node is None:
            if source.strip():
                raise ValueError(f"Canonical reads require the declared handler class {class_name!r}")
            return f"class {class_name}:\n" + "\n".join(
                "\n".join("    " + line if line else "" for line in function.splitlines()) + "\n"
                for function in functions.values()
            )
        body = class_node.body
    lines = source.splitlines(keepends=True)
    edits: list[tuple[int, int, list[str]]] = []
    remaining = dict(functions)
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in remaining:
            rendered = remaining.pop(node.name)
            indent = " " * node.col_offset
            start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)]) - 1
            edits.append((start, node.end_lineno or node.lineno, [
                indent + line + "\n" if line else "\n" for line in rendered.splitlines()
            ]))
    if remaining:
        indent = "    " if class_node else ""
        insertion = class_node.end_lineno if class_node else len(lines)
        assert insertion is not None
        rendered_lines = ["\n"]
        for rendered in remaining.values():
            rendered_lines.extend(indent + line + "\n" if line else "\n" for line in rendered.splitlines())
            rendered_lines.append("\n")
        edits.append((insertion, insertion, rendered_lines))
        if class_node:
            for node in body:
                if isinstance(node, ast.Pass):
                    edits.append((node.lineno - 1, node.end_lineno or node.lineno, []))
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = replacement
    rendered_source = "".join(lines)
    ast.parse(rendered_source)
    return rendered_source


def _read_functions(module_id: str, collection: dict[str, Any], operation: str) -> tuple[str, str, str]:
    name = collection["name"]
    method = canonical_read_action_id(name, operation)
    params = "page=1, page_size=20, search=None" if operation == "list" else "id"
    arguments = "page=page, page_size=page_size, search=search" if operation == "list" else "id=id"
    handler = (
        f"async def {method}(self, ctx, *, {params}):\n"
        "    from . import service\n"
        f"    return await service.{method}(ctx, {arguments})\n"
    )
    response_fields = [field["name"] for field in collection.get("fields") or []]
    service = (
        f"async def {method}(ctx, *, {params}):\n"
        "    from . import repo\n"
        f"    result = await repo.{method}(ctx, {arguments})\n"
        f"    fields = {response_fields!r}\n"
    )
    if operation == "list":
        service += (
            "    items = [{field: str(record[field]) if field == '_id' else record[field] "
            "for field in fields if field in record} for record in result['items']]\n"
            "    return {'items': items, 'total': result['total']}\n"
        )
    else:
        service += (
            "    record = result['item']\n"
            "    item = {field: str(record[field]) if field == '_id' else record[field] "
            "for field in fields if field in record}\n"
            "    return {'item': item}\n"
        )
    repo = (
        f"async def {method}(ctx, *, {params}):\n"
        "    from .policy import scoped_query\n"
    )
    if operation == "list":
        searchable = [field["name"] for field in collection.get("fields") or [] if field.get("type") == "string"]
        repo += (
            "    import re\n"
            "    page = max(1, int(page))\n"
            "    page_size = max(1, min(100, int(page_size)))\n"
            "    filters = {}\n"
            f"    search_fields = {searchable!r}\n"
            "    if search and search_fields:\n"
            "        filters['$or'] = [{field: {'$regex': re.escape(str(search)), '$options': 'i'}} "
            "for field in search_fields]\n"
            f"    query = scoped_query(ctx, filters, entity_name={name!r})\n"
            f"    collection = ctx.persistence.collection({module_id!r}, {name!r})\n"
            "    total = await collection.count(query)\n"
            "    records = await collection.aggregate([{'$match': query}, {'$sort': {'_id': 1}}, "
            "{'$skip': (page - 1) * page_size}, {'$limit': page_size}])\n"
            "    return {'items': records, 'total': total}\n"
        )
    else:
        declared_fields = {field["name"] for field in collection.get("fields") or []}
        lookup = collection.get("search_by") or ("id" if "id" in declared_fields else "_id")
        if lookup != "_id" and lookup not in declared_fields:
            raise ValueError(f"{module_id}: collection {name!r} search_by {lookup!r} must name a declared field")
        repo += f"    filters = {{{lookup!r}: id}}\n"
        if lookup == "_id":
            repo += (
                "    from bson import ObjectId\n"
                "    if ObjectId.is_valid(id):\n"
                "        filters = {'_id': {'$in': [id, ObjectId(id)]}}\n"
            )
        repo += (
            f"    query = scoped_query(ctx, filters, entity_name={name!r})\n"
            f"    collection = ctx.persistence.collection({module_id!r}, {name!r})\n"
            "    record = await collection.find_one(query)\n"
            "    if record is None:\n"
            "        from mozaiksai.core.runtime import ModuleRecordNotFoundError\n"
            "        raise ModuleRecordNotFoundError('Record not found')\n"
            "    return {'item': record}\n"
        )
    return handler, service, repo


def materialize_module_read_implementations(
    files: Mapping[str, str], *, app_build_plan: Any, data_contract: Any = None,
    owned_paths: list[str] | set[str] | None = None,
    subscription_contract: Any = None,
) -> dict[str, str]:
    """Compile canonical handler/service/repo functions within task-owned paths.

    Supply admitted module manifests alongside the task's candidate sources.
    Scope ``owned_paths`` before task ownership validation; omit it at assembly.
    """
    plan = detach(app_build_plan)
    if not isinstance(plan, dict):
        return {}
    contract = detach(data_contract)
    changed: dict[str, str] = {}
    allowed = set(owned_paths) if owned_paths is not None else None
    for path, content in files.items():
        match = re.fullmatch(r"modules/([^/]+)/module\.yaml", path)
        if not match:
            continue
        module_id = match[1]
        manifest = yaml.safe_load(content)
        collections = _owned_collections(module_id, plan, contract)
        if not collections:
            continue
        actions = manifest.get("actions") or []
        action_map = {action["id"]: action for action in actions}
        canonical_ids = {
            canonical_read_action_id(collection["name"], operation)
            for collection in collections for operation in ("get", "list")
        }
        protected = _protected_writes(
            actions, canonical_ids, module_id=module_id, subscription_contract=subscription_contract,
        )
        functions: list[dict[str, str]] = [{}, {}, {}]
        for collection in collections:
            if collection["tenancy"] == "app_wide" and protected:
                continue  # Explicit protected reads keep their authored access implementation.
            for operation in ("get", "list"):
                method = canonical_read_action_id(collection["name"], operation)
                if method not in action_map:
                    raise ValueError(f"{module_id}: construct {method!r} in module.yaml before its implementation")
                if collection["tenancy"] == "app_wide" and _authored_app_wide_access(action_map[method]):
                    continue
                for layer, source in zip(functions, _read_functions(module_id, collection, operation), strict=True):
                    layer[method] = source
        if not functions[0]:
            continue
        for index, filename in enumerate(("handler.py", "service.py", "repo.py")):
            target = f"modules/{module_id}/backend/{filename}"
            if allowed is not None and target not in allowed:
                continue
            class_name = None
            if filename == "handler.py":
                entrypoint = str(manifest.get("module", {}).get("handler") or "")
                if not re.fullmatch(r"backend\.handler:[A-Za-z_][A-Za-z0-9_]*", entrypoint):
                    raise ValueError(f"{module_id}: canonical reads require module.handler=backend.handler:ClassName")
                class_name = entrypoint.split(":", 1)[1]
            source = files.get(target, "")
            rendered = _replace_functions(source, functions[index], class_name=class_name)
            if rendered != source:
                changed[target] = rendered
    return changed
