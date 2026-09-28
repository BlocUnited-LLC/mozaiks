"""Construct canonical writes and typed record shapes from approved entity ownership.

Every collection a generated module writes through module actions receives
code-owned ``create_<entity>``, ``update_<entity>`` and ``delete_<entity>``
actions, a code-rendered ``backend/schemas.py`` and the handler, service and
repository functions that implement them. The data contract decides the record
id field, the stamped timestamps and the writable fields; the runtime scopes
and stamps ownership. ServiceAgent adds business logic only through the
optional module-level hooks the rendered service calls.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

import yaml

from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    _materialize_schema_contract,
    _unwrap_output_envelope,
    extract_code_file_map_from_payload,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    CANONICAL_WRITE_OPERATIONS,
    canonical_read_action_id,
    canonical_write_action_id,
    collection_has_canonical_writes,
    entity_identifier,
)
from mozaiksai.core.workflow.generator_support.module_read_actions import (
    _owned_collections,
    _replace_functions,
    close_module_read_actions,
)

logger = logging.getLogger(__name__)

# Logical contract types map to one closed request type and one Python annotation.
# Structured types have no closed request representation; canonical writes
# leave them to defaults, hooks and custom mutations.
_FIELD_TYPES: dict[str, tuple[str | None, str]] = {
    "string": ("string", "str"), "str": ("string", "str"), "text": ("string", "str"),
    "uuid": ("string", "str"), "email": ("string", "str"), "url": ("string", "str"),
    "boolean": ("boolean", "bool"), "bool": ("boolean", "bool"),
    "integer": ("integer", "int"), "int": ("integer", "int"),
    "number": ("number", "float"), "float": ("number", "float"), "double": ("number", "float"),
    "decimal": ("number", "float"),
    "date": ("string", "datetime"), "datetime": ("string", "datetime"), "timestamp": ("string", "datetime"),
    "object": (None, "dict[str, Any]"), "dict": (None, "dict[str, Any]"), "map": (None, "dict[str, Any]"),
    "array": (None, "list[Any]"), "list": (None, "list[Any]"),
}
_DATE_TYPES = frozenset({"date", "datetime", "timestamp"})
_TIMESTAMP_FIELDS = {"created_at": "created_at", "updated_at": "updated_at"}
_RESERVED_SCOPE_FIELDS = frozenset({"app_id", "tenant_id", "workspace_id", "user_id"})
_RESTRICTED_SURFACES = frozenset({"internal", "admin_internal"})


def _field_type(field: Mapping[str, Any], location: str) -> tuple[str | None, str]:
    kind = str(field.get("type") or "").strip().lower()
    if kind not in _FIELD_TYPES:
        raise ValueError(
            f"{location}: field {field.get('name')!r} type {field.get('type')!r} is not a canonical "
            f"contract type; choose one of {sorted(_FIELD_TYPES)}"
        )
    return _FIELD_TYPES[kind]


def _parse_default(field: Mapping[str, Any]) -> tuple[bool, Any]:
    raw = field.get("default")
    if raw is None:
        return False, None
    if not isinstance(raw, str):
        return True, raw
    try:
        return True, json.loads(raw)
    except ValueError:
        return True, raw


def _pascal(identifier: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in identifier.split("_") if part)


def collection_record_shape(module_id: str, collection: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve every code-owned decision about one collection's records."""
    name = str(collection.get("name") or "")
    location = f"{module_id}: collection {name!r}"
    fields = [field for field in collection.get("fields") or [] if isinstance(field, Mapping)]
    names = [str(field["name"]) for field in fields]
    if len(names) != len(set(names)):
        raise ValueError(f"{location}: field names must be unique")
    owner_field = collection.get("owner_field")
    lookup = collection.get("search_by") or ("id" if "id" in names else "_id")
    if lookup != "_id" and lookup not in names:
        raise ValueError(f"{location}: search_by {lookup!r} must name a declared field")
    if lookup == owner_field:
        raise ValueError(f"{location}: search_by {lookup!r} cannot be the owner field")
    entity = str(collection.get("entity") or "")
    identifier = entity_identifier(entity)
    timestamps: dict[str, str] = {}
    writable: list[dict[str, Any]] = []
    for field in fields:
        field_name = str(field["name"])
        json_type, annotation = _field_type(field, location)
        if field_name in _TIMESTAMP_FIELDS and str(field.get("type") or "").lower() in _DATE_TYPES:
            timestamps[_TIMESTAMP_FIELDS[field_name]] = field_name
            continue
        if field_name in {"_id", lookup, owner_field} or field_name in _RESERVED_SCOPE_FIELDS:
            continue
        has_default, default = _parse_default(field)
        required = bool(field.get("required")) and not has_default
        if json_type is None and required:
            raise ValueError(
                f"{location}: required field {field_name!r} has a structured type {field.get('type')!r} "
                "that canonical create input cannot carry; declare a default, make it optional, or "
                "capture it through a custom mutation"
            )
        writable.append({
            "name": field_name, "json_type": json_type, "annotation": annotation,
            "required": required, "has_default": has_default, "default": default,
            "enum": list(field["enum"]) if field.get("enum") else None,
            "nullable": bool(field.get("nullable")),
        })
    return {
        "name": name, "entity": entity, "identifier": identifier, "class_name": _pascal(identifier),
        "constant": identifier.upper(), "fields": fields, "field_names": names,
        "id_field": lookup, "owner_field": owner_field, "timestamps": timestamps, "writable": writable,
        "request_fields": [field for field in writable if field["json_type"] is not None],
    }


def _record_schema(shape: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in shape["fields"]:
        field_name = str(field["name"])
        kind = str(field.get("type") or "").lower()
        declaration: dict[str, Any] = {}
        json_type = "string" if field_name == "_id" else _FIELD_TYPES.get(kind, (None, ""))[0]
        if json_type is not None and kind not in _DATE_TYPES:
            declaration["type"] = [json_type, "null"] if field.get("nullable") else json_type
        elif kind in {"object", "dict", "map", "array", "list"}:
            declaration["type"] = "object" if kind in {"object", "dict", "map"} else "array"
        if field.get("enum"):
            declaration["enum"] = [*field["enum"], *([None] if field.get("nullable") else [])]
        properties[field_name] = declaration
        if field.get("required"):
            required.append(field_name)
    result: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def _request_property(field: dict[str, Any]) -> dict[str, Any]:
    declaration: dict[str, Any] = {"type": field["json_type"]}
    if field["enum"]:
        declaration["enum"] = list(field["enum"])
    return declaration


def _request_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    import_closed_contract_schema(schema)
    return schema


def _write_action(shape: dict[str, Any], operation: str) -> dict[str, Any]:
    action_id = canonical_write_action_id(shape["entity"], operation)
    entity, id_field = shape["entity"], shape["id_field"]
    writable = {field["name"]: _request_property(field) for field in shape["request_fields"]}
    if operation == "create":
        input_schema = _request_schema(writable, [field["name"] for field in shape["request_fields"] if field["required"]])
        description = (
            f"Create one {entity} record. The record id, ownership and declared timestamps are "
            "assigned by code; the runtime stamps the authenticated owner."
        )
    elif operation == "update":
        input_schema = _request_schema({id_field: {"type": "string"}, **writable}, [id_field])
        description = (
            f"Update the declared fields of one {entity} record addressed by {id_field} within the "
            "requesting principal's declared data scope; omitted fields keep their stored values."
        )
    else:
        input_schema = _request_schema({id_field: {"type": "string"}}, [id_field])
        description = (
            f"Delete one {entity} record addressed by {id_field} within the requesting principal's "
            "declared data scope."
        )
    output_schema: dict[str, Any] = (
        {"type": "object", "properties": {"deleted": {"type": "boolean"}}, "required": ["deleted"]}
        if operation == "delete" else
        {"type": "object", "properties": {"item": _record_schema(shape)}, "required": ["item"]}
    )
    return {
        "id": action_id, "description": description, "handler_method": action_id, "api_surface": None,
        "input_schema": input_schema, "output_schema": output_schema, "permissions": [], "emits": [],
        "entitlement_gate": None, "ask_context_safe": False,
    }


def _write_collections(module_id: str, plan: dict[str, Any], contract: Any) -> list[dict[str, Any]]:
    collections = [
        collection for collection in _owned_collections(module_id, plan, contract)
        if collection_has_canonical_writes(collection)
    ]
    seen: dict[str, str] = {}
    for collection in collections:
        action_id = canonical_write_action_id(collection["entity"], "create")
        if action_id in seen:
            raise ValueError(
                f"{module_id}: entities {seen[action_id]!r} and {collection['entity']!r} produce the same "
                f"canonical write ids; declare distinct entity names"
            )
        seen[action_id] = collection["entity"]
    return collections


def _normalize_prior_access(
    module_id: str, collection: dict[str, Any], prior: dict[str, Any], action_id: str,
) -> None:
    """Canonical writes carry no role permissions; ownership is the runtime's job."""
    permissions = list(prior.get("permissions") or [])
    surface = prior.get("api_surface")
    if collection["tenancy"] == "app_wide":
        if permissions or surface is not None:
            raise ValueError(
                f"{module_id}: app_wide collection {collection['name']!r} canonical write {action_id!r} "
                f"requires permissions {permissions!r} and api_surface {surface!r}, but config/auth.yaml "
                "declares no role that grants them. Declare per_user or per_workspace tenancy for "
                "owner-scoped writes, or declare a custom mutation for restricted app-wide writes."
            )
        return
    if permissions or surface is not None:
        logger.warning(
            "CANONICAL_WRITE_NORMALIZED: module=%s action=%s collection=%s tenancy=%s stripped "
            "permissions=%r api_surface=%r; no declared auth role grants them and runtime "
            "persistence enforces ownership",
            module_id, action_id, collection["name"], collection["tenancy"], permissions, surface,
        )


def _reject_protected_app_wide_writes(
    module_id: str, collection: dict[str, Any], actions: list[dict[str, Any]], canonical_ids: set[str],
) -> None:
    if collection["tenancy"] != "app_wide":
        return
    protected = sorted(
        str(action.get("id")) for action in actions
        if action.get("id") not in canonical_ids and action.get("permissions")
    )
    if protected:
        raise ValueError(
            f"{module_id}: app_wide collection {collection['name']!r} has protected writes {protected!r} "
            "that require role permissions, but config/auth.yaml declares no role that grants them; "
            "canonical writes cannot be constructed beside them. Declare per_user or per_workspace "
            "tenancy for owner-scoped writes, or remove the role permissions."
        )


def _close_manifest_writes(module_id: str, manifest: dict[str, Any], plan: dict[str, Any], contract: Any) -> None:
    collections = _write_collections(module_id, plan, contract)
    if not collections:
        return
    actions = manifest.setdefault("actions", [])
    existing = {action["id"]: action for action in actions if action.get("id")}
    canonical_ids = {
        canonical_write_action_id(collection["entity"], operation)
        for collection in collections for operation in CANONICAL_WRITE_OPERATIONS
    }
    for collection in collections:
        _reject_protected_app_wide_writes(module_id, collection, actions, canonical_ids)
        shape = collection_record_shape(module_id, collection)
        for operation in CANONICAL_WRITE_OPERATIONS:
            action = _write_action(shape, operation)
            prior = existing.get(action["id"])
            if prior is None:
                actions.append(action)
                existing[action["id"]] = action
                continue
            _normalize_prior_access(module_id, collection, prior, action["id"])
            # Declared events and approved gates are business decisions; keep them.
            action["emits"] = list(prior.get("emits") or [])
            action["entitlement_gate"] = prior.get("entitlement_gate")
            prior.clear()
            prior.update(action)


def _require_declared_design_actions(module_id: str, manifest: dict[str, Any], design_surface_map: Any) -> None:
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
            "access policies; ServiceAgent must implement declared custom reads and custom mutations"
        )


def close_module_actions(
    payload: Any, *, app_build_plan: Any, data_contract: Any = None, design_surface_map: Any = None,
    subscription_contract: Any = None,
) -> Any:
    """Return detached output with every canonical write and read declared.

    Writes close first so an app-wide access conflict is reported as the write
    decision it is, before read closure asks for explicit read declarations.
    """
    output = _unwrap_output_envelope(detach(payload))
    plan = detach(app_build_plan)
    if not isinstance(output, dict) or not isinstance(plan, dict):
        return output
    contract = detach(data_contract)
    if contract is not None and not isinstance(contract, dict):
        raise ValueError("Write action closure requires a structured data_contract")
    bundle = output.get("module_contract")
    if not isinstance(bundle, dict):
        files = extract_code_file_map_from_payload(output)
        changes = materialize_module_actions(
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
        raise ValueError("Write action closure requires matching module_contract and module.yaml identities")
    _close_manifest_writes(module_id, manifest, plan, contract)
    output = close_module_read_actions(
        output, app_build_plan=plan, data_contract=contract, design_surface_map=design_surface_map,
        subscription_contract=subscription_contract,
    )
    _require_declared_design_actions(module_id, output["module_contract"]["module_yaml"], design_surface_map)
    return output


def materialize_module_actions(
    files_map: Mapping[str, str], *, app_build_plan: Any, data_contract: Any = None,
    design_surface_map: Any = None, subscription_contract: Any = None,
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
        closed = close_module_actions(
            payload, app_build_plan=app_build_plan, data_contract=data_contract,
            design_surface_map=design_surface_map, subscription_contract=subscription_contract,
        )
        expanded = closed["module_contract"]["module_yaml"]
        for action in expanded.get("actions") or []:
            for key in ("input_schema", "output_schema"):
                if isinstance(action.get(key), dict):
                    action[key] = _materialize_schema_contract(action[key], closed_request=key == "input_schema")
        if expanded != manifest:
            changed[path] = yaml.safe_dump(expanded, sort_keys=False, allow_unicode=True)
    return changed


# --------------------------------------------------------------------------- schemas.py


def _annotation(field: dict[str, Any]) -> str:
    return f"{field['annotation']} | None" if field["nullable"] else str(field["annotation"])


def _render_record_class(shape: dict[str, Any]) -> str:
    lines = [f"class {shape['class_name']}Record(TypedDict, total=False):"]
    lines.append(f'    """One stored {shape["entity"]} document; every key is a declared contract field."""')
    for field in shape["fields"]:
        kind = str(field.get("type") or "").lower()
        annotation = _FIELD_TYPES[kind][1]
        if field.get("nullable"):
            annotation = f"{annotation} | None"
        lines.append(f"    {field['name']}: {annotation}")
    if shape["id_field"] == "_id":
        lines.append("    _id: Any")
    return "\n".join(lines) + "\n"


def _render_input_classes(shape: dict[str, Any]) -> str:
    create = [f"class {shape['class_name']}CreateInput(TypedDict, total=False):"]
    create.append(f'    """Writable fields accepted by create_{shape["identifier"]}."""')
    update = [f"class {shape['class_name']}UpdateInput(TypedDict, total=False):"]
    update.append(f'    """Writable fields accepted by update_{shape["identifier"]}."""')
    for field in shape["writable"]:
        create.append(f"    {field['name']}: {_annotation(field)}")
        update.append(f"    {field['name']}: {_annotation(field)}")
    if len(create) == 2:
        create.append("    pass")
        update.append("    pass")
    return "\n".join(create) + "\n\n\n" + "\n".join(update) + "\n"


def _render_collection_schema(module_id: str, shape: dict[str, Any]) -> str:
    prefix, identifier = shape["constant"], shape["identifier"]
    writable = tuple(field["name"] for field in shape["writable"])
    required = tuple(field["name"] for field in shape["writable"] if field["required"])
    defaults = {field["name"]: field["default"] for field in shape["writable"] if field["has_default"]}
    serializer = (
        f"def serialize_{identifier}(record: Mapping[str, Any]) -> dict[str, Any]:\n"
        f'    """Project a stored {shape["entity"]} document onto its declared fields only."""\n'
        f"    return {{\n"
        f"        field: str(record[field]) if field == '_id' else record[field]\n"
        f"        for field in {prefix}_FIELDS if field in record\n"
        f"    }}\n"
    )
    new_id = (
        f"def new_{identifier}_id() -> str:\n"
        f'    """Assign the declared {shape["id_field"]} value for a new {shape["entity"]} record."""\n'
        f"    return uuid.uuid4().hex\n"
    ) if shape["id_field"] != "_id" else ""
    return (
        f"{_render_record_class(shape)}\n\n{_render_input_classes(shape)}\n\n"
        f"{prefix}_COLLECTION = {shape['name']!r}\n"
        f"{prefix}_FIELDS = {tuple(shape['field_names']) + (('_id',) if shape['id_field'] == '_id' else ())!r}\n"
        f"{prefix}_ID_FIELD = {shape['id_field']!r}\n"
        f"{prefix}_OWNER_FIELD = {shape['owner_field']!r}\n"
        f"{prefix}_CREATED_AT_FIELD = {shape['timestamps'].get('created_at')!r}\n"
        f"{prefix}_UPDATED_AT_FIELD = {shape['timestamps'].get('updated_at')!r}\n"
        f"{prefix}_WRITABLE_FIELDS = {writable!r}\n"
        f"{prefix}_REQUIRED_CREATE_FIELDS = {required!r}\n"
        f"{prefix}_DEFAULTS: dict[str, Any] = {defaults!r}\n\n\n"
        f"{serializer}\n\n"
        f"def {identifier}_create_values(values: Mapping[str, Any]) -> dict[str, Any]:\n"
        f'    """Keep declared writable fields and apply declared defaults for omitted ones."""\n'
        f"    prepared = {{field: values[field] for field in {prefix}_WRITABLE_FIELDS if field in values}}\n"
        f"    for field, default in {prefix}_DEFAULTS.items():\n"
        f"        prepared.setdefault(field, copy.deepcopy(default))\n"
        f"    return prepared\n\n\n"
        f"def {identifier}_update_changes(values: Mapping[str, Any]) -> dict[str, Any]:\n"
        f'    """Keep only declared writable fields; ids, ownership and timestamps are never client-set."""\n'
        f"    return {{field: values[field] for field in {prefix}_WRITABLE_FIELDS if field in values}}\n"
        + (f"\n\n{new_id}" if new_id else "")
    )


def render_module_schemas(module_id: str, collections: list[dict[str, Any]]) -> str:
    """Compile the one record representation handler, service and repo share."""
    shapes = [collection_record_shape(module_id, collection) for collection in collections]
    if not shapes:
        raise ValueError(f"data_contract module {module_id!r} requires at least one collection")
    uses_uuid = any(shape["id_field"] != "_id" for shape in shapes)
    uses_datetime = any(
        _FIELD_TYPES[str(field.get("type") or "").lower()][1] == "datetime"
        for shape in shapes for field in shape["fields"]
    )
    header = (
        '"""Typed record shapes compiled from data/contract.json.\n\n'
        "Records are plain dicts; the TypedDict classes document their keys. Code\n"
        "renders this file, so module logic imports these names instead of\n"
        "restating field lists.\n"
        '"""\n'
        "from __future__ import annotations\n\n"
        "import copy\n"
        + ("import uuid\n" if uses_uuid else "")
        + "from collections.abc import Mapping\n"
        + ("from datetime import datetime\n" if uses_datetime else "")
        + "from typing import Any, TypedDict\n"
    )
    return header + "\n\n" + "\n\n".join(_render_collection_schema(module_id, shape) for shape in shapes)


def materialize_module_schemas(
    files: Mapping[str, str], *, app_build_plan: Any, data_contract: Any = None,
    module_ids: set[str] | None = None,
) -> dict[str, str]:
    """Render schemas.py for every module owning approved collections."""
    plan = detach(app_build_plan)
    contract = detach(data_contract)
    if not isinstance(plan, dict) or not isinstance(contract, dict):
        return {}
    selected = module_ids
    if selected is None:
        selected = {parts[1] for path in files if len(parts := path.split("/")) >= 3 and parts[0] == "modules"}
    result: dict[str, str] = {}
    for module_id in sorted(selected):
        collections = _owned_collections(module_id, plan, contract)
        if not collections:
            continue
        path = f"modules/{module_id}/backend/schemas.py"
        result[path] = render_module_schemas(module_id, collections)
        if path in files and files[path] != result[path]:
            raise ValueError(
                f"{path}: schemas.py is rendered from data_contract; omit model-authored schema source and "
                "keep module-specific helpers in service.py"
            )
    return result


def materialize_task_module_schemas(
    files: Mapping[str, str], *, task: Mapping[str, Any], app_build_plan: Any, data_contract: Any = None,
) -> dict[str, str]:
    """Render owned schema artifacts before batch output ownership validation."""
    selected = {
        parts[1] for path in [*(task.get("owned_paths") or []), *files]
        if len(parts := str(path).split("/")) == 4
        and parts[0] == "modules" and parts[2:] == ["backend", "schemas.py"]
    }
    if not selected:
        return {}
    return materialize_module_schemas(files, app_build_plan=app_build_plan, data_contract=data_contract, module_ids=selected)


# --------------------------------------------------------------------------- implementations


def _hook_call(name: str, arguments: str, *, assign: str | None = None) -> str:
    call = f"await hook({arguments})"
    return (
        f"    hook = globals().get({name!r})\n"
        f"    if hook is not None:\n"
        f"        {assign + ' = ' if assign else ''}{call}\n"
    )


def _write_functions(module_id: str, shape: dict[str, Any], operation: str) -> tuple[str, str, str]:
    """Render the handler method, service function and repo function for one write."""
    identifier, id_field, prefix = shape["identifier"], shape["id_field"], shape["constant"]
    method = canonical_write_action_id(shape["entity"], operation)
    get_method = canonical_read_action_id(shape["name"], "get")
    lookup = f"{{{id_field!r}: id}}"
    if id_field == "_id":
        lookup = "_id_filter(id)"
    collection = f"    collection = ctx.persistence.collection({module_id!r}, {shape['name']!r})\n"
    not_found = (
        "        from mozaiksai.core.runtime import ModuleRecordNotFoundError\n"
        "        raise ModuleRecordNotFoundError('Record not found')\n"
    )
    stamps = "".join(
        f"    document[{field!r}] = now\n" for field in shape["timestamps"].values()
    )
    if operation == "create":
        handler = (
            f"async def {method}(self, ctx, **values):\n"
            "    from . import service\n"
            f"    return await service.{method}(ctx, values)\n"
        )
        service = (
            f"async def {method}(ctx, values):\n"
            "    from . import repo, schemas\n"
            f"    prepared = schemas.{identifier}_create_values(values)\n"
            f"    missing = [field for field in schemas.{prefix}_REQUIRED_CREATE_FIELDS if field not in prepared]\n"
            "    if missing:\n"
            "        from mozaiksai.core.runtime import ModuleInputValidationError\n"
            "        raise ModuleInputValidationError(f'Missing required fields: {missing}')\n"
            + _hook_call(f"before_{method}", "ctx, prepared", assign="prepared")
            + f"    record = await repo.{method}(ctx, prepared)\n"
            + _hook_call(f"after_{method}", "ctx, record")
            + f"    return {{'item': schemas.serialize_{identifier}(record)}}\n"
        )
        repo = (
            f"async def {method}(ctx, values):\n"
            "    import datetime as _dt\n"
            "    from . import schemas\n"
            "    document = dict(values)\n"
            + (
                f"    if not document.get({id_field!r}):\n"
                f"        document[{id_field!r}] = schemas.new_{identifier}_id()\n"
                if id_field != "_id" else ""
            )
            + ("    now = _dt.datetime.now(_dt.timezone.utc)\n" if stamps else "")
            + stamps
            + collection
            + (
                f"    await collection.insert_one(document)\n"
                f"    record = await collection.find_one({{{id_field!r}: document[{id_field!r}]}})\n"
                if id_field != "_id" else
                "    result = await collection.insert_one(document)\n"
                "    record = await collection.find_one({'_id': result.inserted_id})\n"
            )
            + "    if record is None:\n"
            + not_found
            + "    return record\n"
        )
    elif operation == "update":
        handler = (
            f"async def {method}(self, ctx, **values):\n"
            "    from . import service\n"
            f"    return await service.{method}(ctx, values)\n"
        )
        service = (
            f"async def {method}(ctx, values):\n"
            "    from . import repo, schemas\n"
            f"    id = values.get({id_field!r})\n"
            "    if not isinstance(id, str) or not id:\n"
            "        from mozaiksai.core.runtime import ModuleInputValidationError\n"
            f"        raise ModuleInputValidationError('{id_field} is required')\n"
            f"    changes = schemas.{identifier}_update_changes(values)\n"
            f"    record = (await repo.{get_method}(ctx, id=id))['item']\n"
            + _hook_call(f"before_{method}", "ctx, record, changes", assign="changes")
            + f"    record = await repo.{method}(ctx, id, changes)\n"
            + _hook_call(f"after_{method}", "ctx, record, changes")
            + f"    return {{'item': schemas.serialize_{identifier}(record)}}\n"
        )
        updated_at = shape["timestamps"].get("updated_at")
        repo = (
            f"async def {method}(ctx, id, changes):\n"
            + ("    import datetime as _dt\n" if updated_at else "")
            + "    from mozaiksai.core.runtime import ModuleRecordNotFoundError\n"
            + f"    filters = {lookup}\n"
            + "    update = dict(changes)\n"
            + (f"    update[{updated_at!r}] = _dt.datetime.now(_dt.timezone.utc)\n" if updated_at else "")
            + collection
            + "    if update:\n"
            "        result = await collection.update_one(filters, {'$set': update})\n"
            "        if result.matched_count == 0:\n"
            "            raise ModuleRecordNotFoundError('Record not found')\n"
            "    record = await collection.find_one(filters)\n"
            "    if record is None:\n"
            "        raise ModuleRecordNotFoundError('Record not found')\n"
            "    return record\n"
        )
    else:
        handler = (
            f"async def {method}(self, ctx, *, {id_field}):\n"
            "    from . import service\n"
            f"    return await service.{method}(ctx, {id_field})\n"
        )
        service = (
            f"async def {method}(ctx, id):\n"
            "    from . import repo\n"
            f"    record = (await repo.{get_method}(ctx, id=id))['item']\n"
            + _hook_call(f"before_{method}", "ctx, record")
            + f"    await repo.{method}(ctx, id)\n"
            + _hook_call(f"after_{method}", "ctx, record")
            + "    return {'deleted': True}\n"
        )
        repo = (
            f"async def {method}(ctx, id):\n"
            "    from mozaiksai.core.runtime import ModuleRecordNotFoundError\n"
            f"    filters = {lookup}\n"
            + collection
            + "    result = await collection.delete_one(filters)\n"
            "    if result.deleted_count == 0:\n"
            "        raise ModuleRecordNotFoundError('Record not found')\n"
            "    return {'deleted': True}\n"
        )
    return handler, service, repo


_ID_FILTER = (
    "def _id_filter(id):\n"
    "    from bson import ObjectId\n"
    "    if ObjectId.is_valid(id):\n"
    "        return {'_id': {'$in': [id, ObjectId(id)]}}\n"
    "    return {'_id': id}\n"
)


def materialize_module_write_implementations(
    files: Mapping[str, str], *, app_build_plan: Any, data_contract: Any = None,
    owned_paths: list[str] | set[str] | None = None,
) -> dict[str, str]:
    """Compile canonical write functions within task-owned handler/service/repo paths.

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
        collections = _write_collections(module_id, plan, contract)
        if not collections:
            continue
        declared = {action["id"] for action in manifest.get("actions") or []}
        functions: list[dict[str, str]] = [{}, {}, {}]
        for collection in collections:
            shape = collection_record_shape(module_id, collection)
            if shape["id_field"] == "_id":
                functions[2]["_id_filter"] = _ID_FILTER
            for operation in CANONICAL_WRITE_OPERATIONS:
                method = canonical_write_action_id(shape["entity"], operation)
                if method not in declared:
                    raise ValueError(f"{module_id}: construct {method!r} in module.yaml before its implementation")
                for layer, source in zip(functions, _write_functions(module_id, shape, operation), strict=True):
                    layer[method] = source
        for index, filename in enumerate(("handler.py", "service.py", "repo.py")):
            target = f"modules/{module_id}/backend/{filename}"
            if allowed is not None and target not in allowed:
                continue
            class_name = None
            if filename == "handler.py":
                entrypoint = str(manifest.get("module", {}).get("handler") or "")
                if not re.fullmatch(r"backend\.handler:[A-Za-z_][A-Za-z0-9_]*", entrypoint):
                    raise ValueError(f"{module_id}: canonical writes require module.handler=backend.handler:ClassName")
                class_name = entrypoint.split(":", 1)[1]
            source = files.get(target, "")
            rendered = _replace_functions(source, functions[index], class_name=class_name)
            if rendered != source:
                changed[target] = rendered
    return changed


def canonical_write_shapes(module_id: str, *, app_build_plan: Any, data_contract: Any) -> list[dict[str, Any]]:
    """Expose resolved record shapes for prompts, docs and tests."""
    plan = detach(app_build_plan)
    contract = detach(data_contract)
    if not isinstance(plan, dict):
        return []
    return [
        collection_record_shape(module_id, collection)
        for collection in _write_collections(module_id, plan, contract)
    ]


__all__ = [
    "canonical_write_shapes",
    "close_module_actions",
    "collection_record_shape",
    "materialize_module_actions",
    "materialize_module_schemas",
    "materialize_module_write_implementations",
    "materialize_task_module_schemas",
    "render_module_schemas",
]
