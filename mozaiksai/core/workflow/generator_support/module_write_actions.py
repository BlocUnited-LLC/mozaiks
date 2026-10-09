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

import ast
import logging
import re
from collections.abc import Mapping
from typing import Any

import yaml

from mozaiksai.core.runtime.app.module_loader import CANONICAL_EVENT_PREFIXES
from mozaiksai.core.runtime.app.paths import APP_AUTH_CONFIG_PATH
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    _FILE_ENTRY_LANES,
    _materialize_schema_contract,
    _unwrap_output_envelope,
    auth_required_from_strategy,
    data_contract_requires_auth,
    extract_code_file_map_from_payload,
    extract_deleted_file_paths_from_payload,
)
from mozaiksai.core.workflow.generator_support.data_contract_fields import (
    DATE_FIELD_TYPES,
    STRUCTURED_FIELD_TYPES,
    is_managed_timestamp,
    parse_default,
    record_id_field,
    validate_collection_fields,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    CANONICAL_WRITE_EVENT_VERBS,
    CANONICAL_WRITE_OPERATIONS,
    canonical_read_action_id,
    canonical_write_action_id,
    canonical_write_event_aliases,
    canonical_write_event_for,
    canonical_write_event_type,
    collection_has_canonical_writes,
    entity_identifier,
)
from mozaiksai.core.workflow.generator_support.module_authored_code import (
    parse_rendered_python,
    prune_repository,
    reconcile_emit_literals,
)
from mozaiksai.core.workflow.generator_support.module_read_actions import (
    _owned_collections,
    _replace_functions,
    close_module_read_actions,
)
from mozaiksai.core.workflow.path_ownership import normalize_owned_paths
from mozaiksai.resources import resolve_factory_app_root

logger = logging.getLogger(__name__)

# Canonical logical types map to one closed request type and one Python annotation.
# Structured types have no closed request representation; canonical writes
# leave them to defaults, hooks and custom mutations.
_FIELD_TYPES: dict[str, tuple[str | None, str]] = {
    "string": ("string", "str"), "boolean": ("boolean", "bool"), "integer": ("integer", "int"),
    "number": ("number", "float"), "date": ("string", "datetime"), "datetime": ("string", "datetime"),
    "object": (None, "dict[str, Any]"), "array": (None, "list[Any]"),
}
_RESERVED_SCOPE_FIELDS = frozenset({"app_id", "tenant_id", "workspace_id", "user_id"})
_RESTRICTED_SURFACES = frozenset({"internal", "admin_internal"})
_ANONYMOUS_SURFACES = frozenset({"public", "public_readonly"})
_AUTHORED_ACCESS_KEYS = frozenset({"handler_method", "input_schema", "output_schema", "permissions", "api_surface"})
_HOOK_SIGNATURES: dict[str, tuple[str, ...]] = {
    "before_create": ("ctx", "values"), "after_create": ("ctx", "record"),
    "before_update": ("ctx", "record", "changes"), "after_update": ("ctx", "record", "changes"),
    "before_delete": ("ctx", "record"), "after_delete": ("ctx", "record"),
}
_HOOK_NAME = re.compile(r"^(before|after)_(create|update|delete)_([A-Za-z0-9_]+)$")
_COMPANION_PATHS = {"reactions_yaml": "contracts/reactions.yaml", "notifications_yaml": "contracts/notifications.yaml"}


def _pascal(identifier: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in identifier.split("_") if part)


def collection_record_shape(module_id: str, collection: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve every code-owned decision about one collection's records.

    DesignDocs validates field types, defaults and lookups at save; the checks
    run again here only as a backstop for recorded or hand-built contracts.
    """
    name = str(collection.get("name") or "")
    location = f"{module_id}: collection {name!r}"
    validate_collection_fields(collection, location)
    fields = [field for field in collection.get("fields") or [] if isinstance(field, Mapping)]
    names = [str(field["name"]) for field in fields]
    owner_field = collection.get("owner_field")
    id_field = record_id_field(collection, names)
    entity = str(collection.get("entity") or "")
    identifier = entity_identifier(entity)
    timestamps: dict[str, str] = {}
    writable: list[dict[str, Any]] = []
    for field in fields:
        field_name = str(field["name"])
        kind = str(field["type"])
        json_type, annotation = _FIELD_TYPES[kind]
        if is_managed_timestamp(field):
            timestamps[field_name] = field_name
            continue
        if field_name in {"_id", id_field, owner_field} or field_name in _RESERVED_SCOPE_FIELDS:
            continue
        has_default, default = parse_default(field, location)
        writable.append({
            "name": field_name, "json_type": json_type, "annotation": annotation,
            "required": bool(field.get("required")) and not has_default,
            "has_default": has_default, "default": default,
            "enum": list(field["enum"]) if field.get("enum") else None,
            "nullable": bool(field.get("nullable")),
        })
    return {
        "name": name, "entity": entity, "identifier": identifier, "class_name": _pascal(identifier),
        "constant": identifier.upper(), "fields": fields, "field_names": names,
        "id_field": id_field, "search_by": collection.get("search_by"), "owner_field": owner_field,
        "timestamps": timestamps, "writable": writable,
        "request_fields": [field for field in writable if field["json_type"] is not None],
    }


def _record_schema(shape: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in shape["fields"]:
        field_name = str(field["name"])
        kind = str(field["type"])
        declaration: dict[str, Any] = {}
        if field_name == "_id":
            declaration["type"] = "string"
        elif kind in STRUCTURED_FIELD_TYPES:
            declaration["type"] = kind
        elif kind not in DATE_FIELD_TYPES:
            declaration["type"] = [kind, "null"] if field.get("nullable") else kind
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
            f"Create one {entity} record. The record id ({id_field}), ownership and declared timestamps are "
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


def _canonical_read_ids(module_id: str, plan: dict[str, Any], contract: Any) -> set[str]:
    return {
        canonical_read_action_id(collection["name"], operation)
        for collection in _owned_collections(module_id, plan, contract) for operation in ("get", "list")
    }


# --------------------------------------------------------------------------- access policy


def auth_contract_scopes(
    files: Mapping[str, str], *, app_build_plan: Any = None, data_contract: Any = None,
) -> frozenset[str]:
    """Resolve declared scopes from admitted auth or its approved default scaffold.

    Requested scopes establish generation intent, not proof of an issuer grant.
    The runtime still enforces the caller's token scopes. Plan roles and module
    permission catalogues do not supply a role-to-permission grant mapping.
    Before auth scaffolding runs, authenticated generation uses that scaffold's
    actual template; a supplied auth file always takes precedence.
    """
    raw = files.get("config/auth.yaml")
    plan = detach(app_build_plan) or {}
    if not isinstance(plan, dict):
        raise ValueError("Generated action authorization requires a structured app_build_plan")
    if raw is None and (
        auth_required_from_strategy(plan.get("auth_strategy"), roles=plan.get("roles"), field="app_build_plan.auth_strategy")
        or data_contract_requires_auth(detach(data_contract))
    ):
        root = resolve_factory_app_root()
        if root is None:
            raise ValueError("Generated action authorization requires the Factory auth scaffold")
        raw = (root / "build_context/webapp_builder/templates/config/auth.yaml").read_text(encoding="utf-8")
        raw = raw.replace("{{AUTH_DEFAULT_ROUTE}}", "/")
    if raw is None:
        return frozenset()
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError:
        return frozenset()
    if not isinstance(document, dict):
        return frozenset()
    frontend = document.get("frontend")
    scopes = frontend.get("default_scopes") if isinstance(frontend, dict) else None
    return frozenset(scope for scope in scopes if isinstance(scope, str)) if isinstance(scopes, list) else frozenset()


def _validate_generated_action_permissions(
    module_id: str, manifest: Mapping[str, Any], plan: Mapping[str, Any], scopes: frozenset[str],
) -> None:
    """Reject unresolved restrictions after bounded canonical CRUD normalization."""
    declared_generated = any(
        pack.get("capability_pack_id") == module_id and pack.get("capability_source") == "generated_module"
        for pack in plan.get("capability_packs") or [] if isinstance(pack, dict)
    )
    manifest_path = f"modules/{module_id}/module.yaml"
    authored_by_task = any(
        task.get("task_type") == "module_contract"
        and manifest_path in normalize_owned_paths(task.get("owned_paths"))
        for task in plan.get("build_tasks") or [] if isinstance(task, dict)
    )
    if not declared_generated and not authored_by_task:
        return  # Selected packs and host-authored modules have their own authorization owner.
    for action in manifest.get("actions") or []:
        permissions = action.get("permissions")
        if permissions is None:
            permissions = []
        if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
            raise ValueError(
                f"modules/{module_id}/module.yaml: action {action['id']!r} permissions must be a list of strings"
            )
        unresolved = sorted(set(permissions) - scopes)
        if unresolved:
            raise ValueError(
                f"modules/{module_id}/module.yaml: action {action['id']!r} has unresolved permissions "
                f"{unresolved!r} in the approved generated auth contract; declared scopes={sorted(scopes)!r}. "
                "Resolve the reference against config/auth.yaml frontend.default_scopes or its approved "
                "auth scaffold. Preserve the intended restriction, api_surface and entitlement_gate; "
                "do not remove permissions or substitute an unrelated scope just to pass validation. "
                "If no declared scope expresses the approved policy, revise the authorization contract "
                "before retrying. Plan roles, module permission declarations, and auth changes in this "
                "candidate cannot approve a new grant."
            )


def _approved_gates(module_id: str, subscription_contract: Any) -> dict[str, str]:
    approved = detach(subscription_contract) or {}
    return {
        str(update.get("action_id")): str(update.get("entitlement_gate"))
        for update in approved.get("module_contract_updates") or []
        if update.get("module_id") == module_id and update.get("entitlement_gate")
    }


def _restricted_declaration(action: Mapping[str, Any], approved_gates: Mapping[str, str], granted: frozenset[str]) -> bool:
    """An authored write is restricted by a runtime-only surface, a gate, or satisfiable permissions."""
    if action.get("api_surface") in _RESTRICTED_SURFACES:
        return True
    if action.get("entitlement_gate") or approved_gates.get(str(action.get("id"))):
        return True
    permissions = [str(item) for item in action.get("permissions") or []]
    return bool(permissions) and all(item in granted for item in permissions)


def _protected_siblings(
    actions: list[dict[str, Any]], canonical_read_ids: set[str], *, module_id: str, subscription_contract: Any,
) -> list[str]:
    """Describe every action whose access is restricted, mirroring the read closure."""
    approved_gates = _approved_gates(module_id, subscription_contract)
    protected: list[str] = []
    for action in actions:
        action_id = action.get("id")
        if not isinstance(action_id, str) or action_id in canonical_read_ids:
            continue
        reasons = []
        if action.get("permissions"):
            reasons.append(f"permissions={list(action['permissions'])!r}")
        gate = action.get("entitlement_gate") or approved_gates.get(action_id)
        if gate:
            reasons.append(f"entitlement_gate={gate!r}")
        if action.get("api_surface") in _RESTRICTED_SURFACES:
            reasons.append(f"api_surface={action['api_surface']!r}")
        if reasons:
            protected.append(f"{action_id} ({', '.join(reasons)})")
    return protected


def _authored_write(prior: dict[str, Any] | None) -> bool:
    return prior is not None and _AUTHORED_ACCESS_KEYS.issubset(prior)


def _apply_prior_access(
    module_id: str, collection: dict[str, Any], prior: dict[str, Any], action: dict[str, Any],
    granted: frozenset[str],
) -> list[str]:
    """Carry authored access forward only where the platform can honor it.

    Returns the permission ids removed from the canonical action.
    """
    action_id = action["id"]
    permissions = [str(item) for item in prior.get("permissions") or []]
    surface = prior.get("api_surface")
    if surface in _ANONYMOUS_SURFACES:
        raise ValueError(
            f"{module_id}: canonical write {action_id!r} on collection {collection['name']!r} declares "
            f"api_surface {surface!r}; anonymous canonical writes are never constructed. Use api_surface null "
            "for authenticated writes, or declare a separately named custom mutation with that surface."
        )
    if surface in _RESTRICTED_SURFACES:
        action["api_surface"] = surface  # Authored runtime-only exposure is kept, never widened.
    kept = [item for item in permissions if item in granted]
    stripped = [item for item in permissions if item not in granted]
    if stripped and collection["tenancy"] == "app_wide":
        raise ValueError(
            f"{module_id}: app_wide collection {collection['name']!r} canonical write {action_id!r} requires "
            f"permissions {stripped!r} that no declared auth scope grants (declared grants={sorted(granted)!r}). "
            "Declare per_user or per_workspace tenancy for owner-scoped writes, declare the scope in "
            "config/auth.yaml frontend.default_scopes, or remove the permission."
        )
    if stripped:
        logger.warning(
            "CANONICAL_WRITE_NORMALIZED: module=%s action=%s collection=%s tenancy=%s stripped permissions=%r "
            "(declared auth grants=%r); runtime persistence enforces ownership",
            module_id, action_id, collection["name"], collection["tenancy"], stripped, sorted(granted),
        )
    action["permissions"] = kept
    return stripped


def _referenced_permission_ids(manifest: dict[str, Any], companions: Mapping[str, Any]) -> set[str]:
    referenced: set[str] = set()
    for action in [*(manifest.get("actions") or []), *(manifest.get("capabilities") or [])]:
        if isinstance(action, dict):
            referenced.update(str(item) for item in action.get("permissions") or [])
    reactions = companions.get("reactions_yaml") or {}
    for reaction in reactions.get("reactions") or [] if isinstance(reactions, dict) else []:
        if isinstance(reaction, dict):
            referenced.update(str(item) for item in reaction.get("permissions") or [])
    notifications = companions.get("notifications_yaml") or {}
    for rule in notifications.get("notifications") or [] if isinstance(notifications, dict) else []:
        audience = rule.get("audience") if isinstance(rule, dict) else None
        if isinstance(audience, dict):
            referenced.update(str(item) for item in audience.get("permissions") or [])
    return referenced


def _drop_stripped_permission_declarations(
    module_id: str, manifest: dict[str, Any], stripped: set[str], companions: Mapping[str, Any],
) -> None:
    declared = manifest.get("permissions")
    if not stripped or not isinstance(declared, list):
        return
    referenced = _referenced_permission_ids(manifest, companions)
    removable = {item for item in stripped if item not in referenced}
    if not removable:
        return
    manifest["permissions"] = [
        entry for entry in declared
        if not (isinstance(entry, dict) and entry.get("id") in removable)
    ]
    logger.warning(
        "CANONICAL_WRITE_NORMALIZED: module=%s removed unreferenced permission declarations %r",
        module_id, sorted(removable),
    )


def _close_manifest_writes(
    module_id: str, manifest: dict[str, Any], plan: dict[str, Any], contract: Any, *,
    subscription_contract: Any, granted: frozenset[str], companions: Mapping[str, Any],
) -> None:
    collections = _write_collections(module_id, plan, contract)
    if not collections:
        return
    actions = manifest.setdefault("actions", [])
    existing = {action["id"]: action for action in actions if action.get("id")}
    read_ids = _canonical_read_ids(module_id, plan, contract)
    stripped: set[str] = set()
    for collection in collections:
        shape = collection_record_shape(module_id, collection)
        write_ids = [canonical_write_action_id(shape["entity"], operation) for operation in CANONICAL_WRITE_OPERATIONS]
        protected = _protected_siblings(
            actions, read_ids, module_id=module_id, subscription_contract=subscription_contract,
        ) if collection["tenancy"] == "app_wide" else []
        if protected:
            # An app-wide collection beside restricted actions never receives open writes:
            # each write must be explicitly declared and itself restricted, or it is rejected.
            gates = _approved_gates(module_id, subscription_contract)
            open_writes = [
                action_id for action_id in write_ids
                if not _authored_write(existing.get(action_id))
                or not _restricted_declaration(existing[action_id], gates, granted)
            ]
            if open_writes:
                raise ValueError(
                    f"{module_id}: app_wide collection {collection['name']!r} has protected actions "
                    f"{protected!r}; canonical writes {open_writes!r} are not constructed open. Explicitly declare "
                    "each of them in module.yaml.actions with handler_method, input_schema, output_schema, "
                    "permissions and api_surface, restricted by api_surface internal or admin_internal, an "
                    "approved entitlement gate, or permissions declared in config/auth.yaml "
                    f"(declared grants={sorted(granted)!r}); ServiceAgent implements them. Otherwise declare "
                    "per_user or per_workspace tenancy so code constructs owner-scoped writes."
                )
            continue  # Authored restricted app-wide writes are preserved as declared.
        for operation, action_id in zip(CANONICAL_WRITE_OPERATIONS, write_ids, strict=True):
            action = _write_action(shape, operation)
            prior = existing.get(action_id)
            if prior is None:
                actions.append(action)
                existing[action_id] = action
                continue
            stripped.update(_apply_prior_access(module_id, collection, prior, action, granted))
            # Declared events and approved gates are business decisions; keep them.
            action["emits"] = list(prior.get("emits") or [])
            action["entitlement_gate"] = prior.get("entitlement_gate")
            prior.clear()
            prior.update(action)
    _drop_stripped_permission_declarations(module_id, manifest, stripped, companions)


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


def _companions_from_files(module_id: str, files: Mapping[str, str]) -> dict[str, Any]:
    companions: dict[str, Any] = {}
    for key, relative in _COMPANION_PATHS.items():
        raw = files.get(f"modules/{module_id}/{relative}")
        if raw is None:
            continue
        try:
            document = yaml.safe_load(raw)
        except yaml.YAMLError:
            continue
        if isinstance(document, dict):
            companions[key] = document
    return companions


# --------------------------------------------------------------------------- events


def _built_write_shapes(
    module_id: str, manifest: dict[str, Any], plan: dict[str, Any], contract: Any, subscription_contract: Any,
) -> list[dict[str, Any]]:
    """Record shapes of the collections whose canonical writes code constructs and implements."""
    actions = manifest.get("actions") or []
    read_ids = _canonical_read_ids(module_id, plan, contract)
    shapes = []
    for collection in _write_collections(module_id, plan, contract):
        if collection["tenancy"] == "app_wide" and _protected_siblings(
            actions, read_ids, module_id=module_id, subscription_contract=subscription_contract,
        ):
            continue  # Explicitly declared protected app-wide writes keep their authored contract.
        shapes.append(collection_record_shape(module_id, collection))
    return shapes


def _design_events(module_id: str, design_surface_map: Any) -> list[str]:
    surface_map = detach(design_surface_map) or {}
    return [
        str(event) for surface in surface_map.get("surfaces") or [] if isinstance(surface, dict)
        and surface.get("surface_id") == module_id for event in surface.get("events_emitted") or []
    ]


def _canonical_event_entry(module_id: str, shape: dict[str, Any], operation: str) -> dict[str, Any]:
    verb = CANONICAL_WRITE_EVENT_VERBS[operation]
    return {
        "type": canonical_write_event_type(shape["entity"], operation),
        "version": 1,
        "description": (
            f"A {shape['entity']} record was {verb} by {canonical_write_action_id(shape['entity'], operation)}; "
            "the payload is the stored record's declared fields."
        ),
        "producer": module_id,
        "payload_schema": _record_schema(shape),
    }


def _rename_companion_events(
    document: Any, key: str, rename: Any,
) -> list[str]:
    """Rename event references in reactions/notifications; return one note per rename."""
    notes: list[str] = []
    entries = document.get("reactions" if key == "reactions_yaml" else "notifications") if isinstance(document, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        for field in ("event_type", "on"):
            value = entry.get(field)
            renamed = rename(value) if isinstance(value, str) else None
            if renamed and renamed != value:
                entry[field] = renamed
                notes.append(f"{_COMPANION_PATHS[key]} {entry.get('id')!r}.{field}: {value!r} -> {renamed!r}")
    return notes


def close_module_events(
    module_id: str, manifest: dict[str, Any], events_document: Any, companions: dict[str, Any], *,
    shapes: list[dict[str, Any]], aliases: dict[str, str], design_surface_map: Any,
) -> Any:
    """Declare, emit and reconcile a module's events; return the closed events document.

    Canonical writes own their events: ``domain.<entity>.<created|updated|deleted>``
    (one naming rule) is emitted by the canonical action when the approved design
    or the module contract names it under any spelling, and code renders its
    events.yaml entry with the stored record as payload. A custom event keeps
    its own name; an emit and a declaration that differ only by the required
    ``domain.`` prefix are reconciled to the prefixed name. Anything else is one
    error naming both the emit and the declarations. ``manifest`` and the
    companion documents are edited in place.
    """
    canonical: dict[str, tuple[dict[str, Any], str]] = {
        canonical_write_event_type(shape["entity"], operation): (shape, operation)
        for shape in shapes for operation in CANONICAL_WRITE_OPERATIONS
    }
    action_events = {
        canonical_write_action_id(shape["entity"], operation): event_type
        for event_type, (shape, operation) in canonical.items()
    }

    def own(value: Any) -> str | None:
        event_type = canonical_write_event_for(value, aliases)
        return event_type if event_type in canonical else None

    document = events_document if isinstance(events_document, dict) else None
    entries = [entry for entry in (document or {}).get("events") or [] if isinstance(entry, dict)]
    actions = [action for action in manifest.get("actions") or [] if isinstance(action, dict)]
    approved = {event for event in (own(value) for value in _design_events(module_id, design_surface_map)) if event}
    approved.update(event for action in actions for value in action.get("emits") or [] if (event := own(value)))
    approved.update(event for entry in entries if (event := own(entry.get("type"))))

    notes: list[str] = []
    custom: list[dict[str, Any]] = []

    def is_rendered(entry: dict[str, Any], event_type: str) -> bool:
        expected = _canonical_event_entry(module_id, *canonical[event_type])
        schemas = (expected["payload_schema"], _materialize_schema_contract(expected["payload_schema"]))
        return entry.get("type") == event_type and entry.get("payload_schema") in schemas and all(
            entry.get(key) == expected[key] for key in ("version", "producer", "description")
        )

    replaced = [
        str(entry.get("type")) for entry in entries
        if (event_type := own(entry.get("type"))) and not is_rendered(entry, event_type)
    ]
    if replaced:
        notes.append(f"events.yaml canonical write declarations {replaced!r} are rendered from the record contract")
    renamed: dict[str, str] = {}
    errors: list[str] = []
    for entry in entries:
        if own(entry.get("type")):
            continue
        event_type = str(entry.get("type") or "")
        if event_type and not event_type.startswith(CANONICAL_EVENT_PREFIXES):
            renamed[event_type] = f"domain.{event_type}"
            entry["type"] = renamed[event_type]
            notes.append(f"events.yaml {event_type!r} -> {entry['type']!r} (module events use the domain. prefix)")
        custom.append(entry)
    seen: dict[str, dict[str, Any]] = {}
    for entry in custom:
        prior = seen.get(entry["type"])
        if prior is None:
            seen[entry["type"]] = entry
        elif prior.get("payload_schema") != entry.get("payload_schema"):
            errors.append(
                f"{module_id}: contracts/events.yaml declares {entry['type']!r} twice with different payload "
                "schemas (once without the domain. prefix); keep one declaration"
            )
    rendered = [
        _canonical_event_entry(module_id, *canonical[event_type])
        for event_type in canonical if event_type in approved
    ]
    declared = {*approved, *seen}
    for action in actions:
        own_event = action_events.get(str(action.get("id")))
        original = list(action.get("emits") or [])
        emits: list[str] = [own_event] if own_event in approved else []
        for value in original:
            record_event = own(value)
            if record_event is not None:
                if own_event is None:
                    emits.append(record_event)  # A custom mutation may publish the canonical record event.
                continue  # A canonical write emits only its own event.
            value = renamed.get(value, value)
            if value not in declared and not str(value).startswith(CANONICAL_EVENT_PREFIXES) and f"domain.{value}" in declared:
                value = f"domain.{value}"
            if value not in declared:
                suggestion = value if str(value).startswith(CANONICAL_EVENT_PREFIXES) else f"domain.{value}"
                errors.append(
                    f"{module_id}: module.yaml action {action.get('id')!r} emits {value!r}, but "
                    f"contracts/events.yaml declares {sorted(declared) or 'no events'}. A custom event keeps its "
                    f"own name: declare {suggestion!r} in module_contract.events_yaml (version, producer "
                    f"{module_id!r}, payload_schema) and emit that type, or remove it from the action's emits. "
                    "Canonical record writes emit domain.<entity>.<created|updated|deleted>, which code declares "
                    "and emits."
                )
                continue
            emits.append(str(value))
        emits = list(dict.fromkeys(emits))
        if emits != original:
            action["emits"] = emits
            notes.append(f"module.yaml action {action.get('id')!r} emits {original!r} -> {emits!r}")
    if errors:
        raise ValueError("\n".join(errors))

    def rename(value: str) -> str | None:
        return canonical_write_event_for(value, aliases) or renamed.get(value)

    for key in ("reactions_yaml", "notifications_yaml"):
        notes.extend(_rename_companion_events(companions.get(key), key, rename))
    for note in notes:
        logger.info("CANONICAL_EVENTS_NORMALIZED: module=%s %s", module_id, note)
    events = rendered + list(seen.values())
    if document is None and not events:
        return None
    return {**(document or {}), "schema_version": "mozaiks.events.v1", "events": events}


def close_module_actions(
    payload: Any, *, app_build_plan: Any, data_contract: Any = None, design_surface_map: Any = None,
    subscription_contract: Any = None, declared_auth_scopes: frozenset[str] | None = None,
    companion_files: Mapping[str, str] | None = None, validate_generated_permissions: bool = True,
) -> Any:
    """Return detached output with every canonical write and read declared.

    Writes close first so an app-wide access conflict is reported as the write
    decision it is, before read closure asks for explicit read declarations.
    ``declared_auth_scopes`` come from approved auth; when omitted they are read
    from admitted ``companion_files`` or the approved default auth scaffold.
    Candidate files cannot approve their own permissions. Remaining action
    restrictions must resolve after canonical CRUD normalization.
    """
    output = _unwrap_output_envelope(detach(payload))
    if isinstance(output, dict):
        raw_file_lanes = {lane: output.get(lane) for lane in _FILE_ENTRY_LANES}
        raw_file_lanes["service_foundation_bundle"] = output.get("service_foundation_bundle")
        if APP_AUTH_CONFIG_PATH in extract_code_file_map_from_payload(raw_file_lanes):
            raise ValueError(
                f"Generated task output cannot author {APP_AUTH_CONFIG_PATH}; "
                "the admitted app baseline or save_auth_scaffold owns auth."
            )
        if APP_AUTH_CONFIG_PATH in extract_deleted_file_paths_from_payload(output):
            raise ValueError(
                f"Generated task output cannot delete {APP_AUTH_CONFIG_PATH}; "
                "the admitted app baseline or save_auth_scaffold owns auth."
            )
    plan = detach(app_build_plan)
    if not isinstance(output, dict) or not isinstance(plan, dict):
        return output
    contract = detach(data_contract)
    if contract is not None and not isinstance(contract, dict):
        raise ValueError("Write action closure requires a structured data_contract")
    companion_files = dict(companion_files or {})
    granted = declared_auth_scopes if declared_auth_scopes is not None else auth_contract_scopes(
        companion_files, app_build_plan=plan, data_contract=contract,
    )
    bundle = output.get("module_contract")
    if not isinstance(bundle, dict):
        files = extract_code_file_map_from_payload(output)
        module_inputs = {**companion_files, **files}
        for path in list(module_inputs):
            match = re.fullmatch(r"modules/([^/]+)/module\.yaml", path)
            if match and not any(
                f"modules/{match[1]}/{relative}" in files
                for relative in ("module.yaml", _EVENTS_PATH, *_COMPANION_PATHS.values())
            ):
                # A service task cannot repair an inherited manifest. Retain
                # owners of edited contract companions for event normalization.
                del module_inputs[path]
        changes = materialize_module_actions(
            module_inputs, app_build_plan=plan, data_contract=contract,
            design_surface_map=design_surface_map, subscription_contract=subscription_contract,
            declared_auth_scopes=granted,
        )
        # A manifest's events companion travels with it: code declares canonical write events.
        changes = {
            path: content for path, content in changes.items()
            if path in files or (
                path.endswith(f"/{_EVENTS_PATH}") and path.removesuffix(_EVENTS_PATH) + "module.yaml" in files
            )
        }
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
    companions = {
        key: bundle[key] for key in _COMPANION_PATHS if isinstance(bundle.get(key), dict)
    } or _companions_from_files(module_id, companion_files)
    _close_manifest_writes(
        module_id, manifest, plan, contract, subscription_contract=subscription_contract, granted=granted,
        companions=companions,
    )
    output = close_module_read_actions(
        output, app_build_plan=plan, data_contract=contract, design_surface_map=design_surface_map,
        subscription_contract=subscription_contract,
    )
    closed = output["module_contract"]
    _require_declared_design_actions(module_id, closed["module_yaml"], design_surface_map)
    _close_user_data_scope(module_id, closed["module_yaml"], plan, contract)
    if validate_generated_permissions:
        _validate_generated_action_permissions(module_id, closed["module_yaml"], plan, granted)
    events = close_module_events(
        module_id, closed["module_yaml"], closed.get("events_yaml"),
        {key: closed[key] for key in _COMPANION_PATHS if isinstance(closed.get(key), dict)},
        shapes=_built_write_shapes(module_id, closed["module_yaml"], plan, contract, subscription_contract),
        aliases=canonical_write_event_aliases(contract), design_surface_map=design_surface_map,
    )
    if events is not None or closed.get("events_yaml") is not None:
        closed["events_yaml"] = events
    return output


def per_user_collections(module_id: str, plan: dict[str, Any], contract: Any) -> list[dict[str, Any]]:
    """The module's approved collections whose rows belong to one account."""
    return [
        collection for collection in _owned_collections(module_id, plan, contract)
        if collection.get("tenancy") == "per_user" and collection.get("owner_field")
    ]


def _close_user_data_scope(module_id: str, manifest: dict[str, Any], plan: dict[str, Any], contract: Any) -> None:
    """A module owning per-user rows takes part in account export and deletion."""
    module = manifest.get("module")
    if not isinstance(module, dict) or not per_user_collections(module_id, plan, contract):
        return
    if module.get("user_data_scope") is not True:
        logger.info(
            "USER_DATA_SCOPE_NORMALIZED: module=%s user_data_scope %r -> true; it owns per_user collections, "
            "whose account-data handler code renders",
            module_id, module.get("user_data_scope"),
        )
        module["user_data_scope"] = True


_EVENTS_KEY, _EVENTS_PATH = "events_yaml", "contracts/events.yaml"


def materialize_module_actions(
    files_map: Mapping[str, str], *, app_build_plan: Any, data_contract: Any = None,
    design_surface_map: Any = None, subscription_contract: Any = None,
    declared_auth_scopes: frozenset[str] | None = None,
    pack_owned_manifest_paths: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Render closed module manifests and their event companions for an admitted app bundle."""
    changed: dict[str, str] = {}
    if app_build_plan is None:
        return changed
    granted = declared_auth_scopes if declared_auth_scopes is not None else auth_contract_scopes(
        files_map, app_build_plan=app_build_plan, data_contract=data_contract,
    )
    companion_paths = {_EVENTS_KEY: _EVENTS_PATH, **_COMPANION_PATHS}
    for path, content in files_map.items():
        match = re.fullmatch(r"modules/([^/]+)/module\.yaml", path)
        if not match:
            continue
        manifest = yaml.safe_load(content)
        if not isinstance(manifest, dict):
            raise ValueError(f"{path}: module manifest must be an object")
        module_id = match[1]
        bundle: dict[str, Any] = {"module_id": module_id, "module_yaml": manifest}
        authored: dict[str, Any] = {}
        for key, relative in companion_paths.items():
            raw = files_map.get(f"modules/{module_id}/{relative}")
            if raw is None:
                continue
            try:
                document = yaml.safe_load(raw)
            except yaml.YAMLError:
                continue  # The companion manifest gate reports unparseable YAML.
            if isinstance(document, dict):
                bundle[key] = document
                authored[key] = document
        closed = close_module_actions(
            {"module_contract": bundle}, app_build_plan=app_build_plan, data_contract=data_contract,
            design_surface_map=design_surface_map, subscription_contract=subscription_contract,
            declared_auth_scopes=granted, companion_files=files_map,
            validate_generated_permissions=path not in pack_owned_manifest_paths,
        )["module_contract"]
        expanded = closed["module_yaml"]
        for action in expanded.get("actions") or []:
            for key in ("input_schema", "output_schema"):
                if isinstance(action.get(key), dict):
                    action[key] = _materialize_schema_contract(action[key], closed_request=key == "input_schema")
        if expanded != manifest:
            changed[path] = yaml.safe_dump(expanded, sort_keys=False, allow_unicode=True)
        for key, relative in companion_paths.items():
            document = closed.get(key)
            if not isinstance(document, dict) or (key != _EVENTS_KEY and key not in authored):
                continue
            if key == _EVENTS_KEY:
                for event in document.get("events") or []:
                    if isinstance(event.get("payload_schema"), dict):
                        event["payload_schema"] = _materialize_schema_contract(event["payload_schema"])
            if document != authored.get(key):
                changed[f"modules/{module_id}/{relative}"] = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    return changed


# --------------------------------------------------------------------------- schemas.py


def _annotation(field: dict[str, Any]) -> str:
    return f"{field['annotation']} | None" if field["nullable"] else str(field["annotation"])


def _render_record_class(shape: dict[str, Any]) -> str:
    lines = [f"class {shape['class_name']}Record(TypedDict, total=False):"]
    lines.append(f'    """One stored {shape["entity"]} document; every key is a declared contract field."""')
    for field in shape["fields"]:
        annotation = _FIELD_TYPES[str(field["type"])][1]
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
    nullable = tuple(field["name"] for field in shape["writable"] if field["nullable"] and not field["has_default"])
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
        f'    """Assign the generated {shape["id_field"]} value for a new {shape["entity"]} record."""\n'
        f"    return uuid.uuid4().hex\n"
    ) if shape["id_field"] != "_id" else ""
    return (
        f"{_render_record_class(shape)}\n\n{_render_input_classes(shape)}\n\n"
        f"{prefix}_COLLECTION = {shape['name']!r}\n"
        f"{prefix}_FIELDS = {tuple(shape['field_names']) + (('_id',) if shape['id_field'] == '_id' else ())!r}\n"
        f"{prefix}_ID_FIELD = {shape['id_field']!r}\n"
        f"{prefix}_LOOKUP_FIELD = {shape['search_by'] or shape['id_field']!r}\n"
        f"{prefix}_OWNER_FIELD = {shape['owner_field']!r}\n"
        f"{prefix}_CREATED_AT_FIELD = {shape['timestamps'].get('created_at')!r}\n"
        f"{prefix}_UPDATED_AT_FIELD = {shape['timestamps'].get('updated_at')!r}\n"
        f"{prefix}_WRITABLE_FIELDS = {writable!r}\n"
        f"{prefix}_REQUIRED_CREATE_FIELDS = {required!r}\n"
        f"{prefix}_DEFAULTS: dict[str, Any] = {defaults!r}\n"
        f"{prefix}_NULLABLE_FIELDS = {nullable!r}\n\n\n"
        f"{serializer}\n\n"
        f"def {identifier}_create_values(values: Mapping[str, Any]) -> dict[str, Any]:\n"
        f'    """Keep declared writable fields; apply defaults and store null for omitted nullable fields."""\n'
        f"    prepared = {{field: values[field] for field in {prefix}_WRITABLE_FIELDS if field in values}}\n"
        f"    for field, default in {prefix}_DEFAULTS.items():\n"
        f"        prepared.setdefault(field, copy.deepcopy(default))\n"
        f"    for field in {prefix}_NULLABLE_FIELDS:\n"
        f"        prepared.setdefault(field, None)\n"
        f"    return prepared\n\n\n"
        f"def {identifier}_update_changes(values: Mapping[str, Any]) -> dict[str, Any]:\n"
        f'    """Keep only declared writable fields; ids, ownership and timestamps are never client-set."""\n'
        f"    return {{field: values[field] for field in {prefix}_WRITABLE_FIELDS if field in values}}\n\n\n"
        f"def {identifier}_hook_values(values: Mapping[str, Any], operation: str) -> dict[str, Any]:\n"
        f'    """Allowlist a write hook result; the id and managed timestamps stay code-owned.\n\n'
        f"    A create hook's owner value is left for the runtime to verify (a foreign owner is\n"
        f'    rejected); an update hook can never change the owner.\n'
        f'    """\n'
        + (
            # A create hook's owner value reaches the runtime, which rejects a foreign owner (403).
            f"    allowed = {prefix}_WRITABLE_FIELDS + (({prefix}_OWNER_FIELD,) if operation == 'create' else ())\n"
            if shape["owner_field"] else f"    allowed = {prefix}_WRITABLE_FIELDS\n"
        )
        + f"    stripped = sorted(key for key in values if key not in allowed)\n"
        f"    if stripped:\n"
        f"        logging.getLogger(__name__).warning(\n"
        f"            'HOOK_OUTPUT_STRIPPED: entity={shape['entity']} operation=%s keys=%s are code-owned', operation, stripped,\n"
        f"        )\n"
        f"    return {{field: values[field] for field in allowed if field in values}}\n"
        + (f"\n\n{new_id}" if new_id else "")
    )


def render_module_schemas(module_id: str, collections: list[dict[str, Any]]) -> str:
    """Compile the one record representation handler, service and repo share."""
    shapes = [collection_record_shape(module_id, collection) for collection in collections]
    if not shapes:
        raise ValueError(f"data_contract module {module_id!r} requires at least one collection")
    uses_uuid = any(shape["id_field"] != "_id" for shape in shapes)
    uses_datetime = any(str(field["type"]) in DATE_FIELD_TYPES for shape in shapes for field in shape["fields"])
    header = (
        '"""Typed record shapes compiled from data/contract.json.\n\n'
        "Records are plain dicts; the TypedDict classes document their keys. Code\n"
        "renders this file, so module logic imports these names instead of\n"
        "restating field lists.\n"
        '"""\n'
        "from __future__ import annotations\n\n"
        "import copy\n"
        "import logging\n"
        + ("import uuid\n" if uses_uuid else "")
        + "from collections.abc import Mapping\n"
        + ("from datetime import datetime\n" if uses_datetime else "")
        + "from typing import Any, TypedDict\n"
    )
    return header + "\n\n" + "\n\n".join(_render_collection_schema(module_id, shape) for shape in shapes)


def rendered_schema_names(source: str, *, path: str) -> set[str]:
    """Top-level names a code-rendered schemas.py defines."""
    names: set[str] = set()
    for node in parse_rendered_python(path, source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def materialize_module_schemas(
    files: Mapping[str, str], *, app_build_plan: Any, data_contract: Any = None,
    module_ids: set[str] | None = None,
) -> dict[str, str]:
    """Render schemas.py for every module owning approved collections.

    The file is code-owned: a model-authored copy is replaced and the
    replacement is logged, never rejected.
    """
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
            logger.warning(
                "SCHEMAS_OVERWRITTEN: %s is rendered from data_contract; the authored copy was replaced. "
                "Module-specific helpers belong in service.py.",
                path,
            )
    return result


def code_owned_schema_paths(
    owned_paths: list[str] | tuple[str, ...], *, app_build_plan: Any, data_contract: Any = None,
) -> set[str]:
    """Owned schema paths whose content is entirely rendered from the contract."""
    plan = detach(app_build_plan)
    contract = detach(data_contract)
    if not isinstance(plan, dict) or not isinstance(contract, dict):
        return set()
    result: set[str] = set()
    for path in owned_paths:
        parts = str(path).split("/")
        if len(parts) == 4 and parts[0] == "modules" and parts[2:] == ["backend", "schemas.py"]:
            if _owned_collections(parts[1], plan, contract):
                result.add(str(path))
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


# --------------------------------------------------------------------------- hooks and stale imports


def _hook_signature(kind: str, identifier: str) -> str:
    return f"async def {kind}_{identifier}({', '.join(_HOOK_SIGNATURES[kind])})"


def validate_write_hooks(path: str, source: str, identifiers: list[str]) -> None:
    """Reject hooks the rendered service could not call as written."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"{path}:{exc.lineno}: service source must parse before hook validation") from None
    expected = sorted(f"{kind}_{identifier}" for kind in _HOOK_SIGNATURES for identifier in identifiers)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        match = _HOOK_NAME.match(node.name)
        if match is None:
            continue
        kind = f"{match[1]}_{match[2]}"
        if match[3] not in identifiers:
            raise ValueError(
                f"{path}:{node.lineno}: hook {node.name!r} names no canonical entity of this module; "
                f"expected one of {expected!r}"
            )
        signature = _hook_signature(kind, match[3])
        if node not in tree.body:
            raise ValueError(
                f"{path}:{node.lineno}: hook {node.name!r} must be a module-level function, not a method: {signature}"
            )
        if not isinstance(node, ast.AsyncFunctionDef):
            raise ValueError(f"{path}:{node.lineno}: hook {node.name!r} must be async: {signature}")
        positional = [arg.arg for arg in [*node.args.posonlyargs, *node.args.args]]
        required_keyword = [arg.arg for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True) if default is None]
        if (
            len(positional) != len(_HOOK_SIGNATURES[kind]) or node.args.vararg is not None or required_keyword
        ):
            raise ValueError(
                f"{path}:{node.lineno}: hook {node.name!r} has signature ({', '.join(positional)}); "
                f"expected {signature}"
            )


def _reject_stale_schema_imports(module_id: str, files: Mapping[str, str], rendered_schemas: str) -> None:
    available = rendered_schema_names(rendered_schemas, path=f"modules/{module_id}/backend/schemas.py")
    for filename in ("handler.py", "service.py", "repo.py"):
        path = f"modules/{module_id}/backend/{filename}"
        source = files.get(path)
        if not source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue  # The module implementation gate reports syntax errors.
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 1 or node.module != "schemas":
                continue
            missing = sorted(alias.name for alias in node.names if alias.name != "*" and alias.name not in available)
            if missing:
                raise ValueError(
                    f"{path}:{node.lineno}: imports {missing!r} from .schemas, but the code-rendered schemas.py "
                    f"defines only {sorted(available)!r}. Canonical create/update/delete, list/get and schemas "
                    "are code-owned; author only write hooks, custom mutations and custom reads against them."
                )


# --------------------------------------------------------------------------- implementations


def _hook_call(name: str, arguments: str, *, guard: str | None = None) -> str:
    """Call an optional hook; a None result leaves the payload unchanged."""
    lines = [
        f"    hook = globals().get({name!r})\n",
        "    if hook is not None:\n",
    ]
    if guard is None:
        lines.append(f"        await hook({arguments})\n")
    else:
        target, helper, operation = guard.split(":")
        lines.extend([
            f"        result = await hook({arguments})\n",
            "        if result is not None:\n",
            f"            {target} = schemas.{helper}(result, {operation!r})\n",
        ])
    return "".join(lines)


def _write_functions(
    module_id: str, shape: dict[str, Any], operation: str, event_type: str | None = None,
) -> tuple[str, str, str]:
    """Render the handler method, service function and repo function for one write.

    When the action declares its canonical event, the service emits it with the
    stored record's declared fields once the after hook has returned.
    """
    identifier, id_field, prefix = shape["identifier"], shape["id_field"], shape["constant"]
    respond = (
        f"    item = schemas.serialize_{identifier}(record)\n"
        f"    await ctx.emit({event_type!r}, item)\n"
        "    return {'item': item}\n"
        if event_type else f"    return {{'item': schemas.serialize_{identifier}(record)}}\n"
    )
    method = canonical_write_action_id(shape["entity"], operation)
    load_method = f"load_{identifier}"
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
            + _hook_call(f"before_{method}", "ctx, prepared", guard=f"prepared:{identifier}_hook_values:create")
            + f"    record = await repo.{method}(ctx, prepared)\n"
            + _hook_call(f"after_{method}", "ctx, record")
            + respond
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
            f"    record = await repo.{load_method}(ctx, id)\n"
            + _hook_call(f"before_{method}", "ctx, record, changes", guard=f"changes:{identifier}_hook_values:update")
            + f"    record = await repo.{method}(ctx, id, changes)\n"
            + _hook_call(f"after_{method}", "ctx, record, changes")
            + respond
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
            + ("    from . import repo, schemas\n" if event_type else "    from . import repo\n")
            + f"    record = await repo.{load_method}(ctx, id)\n"
            + _hook_call(f"before_{method}", "ctx, record")
            + f"    await repo.{method}(ctx, id)\n"
            + _hook_call(f"after_{method}", "ctx, record")
            + (f"    await ctx.emit({event_type!r}, schemas.serialize_{identifier}(record))\n" if event_type else "")
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


def _load_function(module_id: str, shape: dict[str, Any]) -> str:
    """Render the repo function that addresses one record by its generated id."""
    id_field = shape["id_field"]
    lookup = "_id_filter(id)" if id_field == "_id" else f"{{{id_field!r}: id}}"
    return (
        f"async def load_{shape['identifier']}(ctx, id):\n"
        f"    collection = ctx.persistence.collection({module_id!r}, {shape['name']!r})\n"
        f"    record = await collection.find_one({lookup})\n"
        "    if record is None:\n"
        "        from mozaiksai.core.runtime import ModuleRecordNotFoundError\n"
        "        raise ModuleRecordNotFoundError('Record not found')\n"
        "    return record\n"
    )


_ID_FILTER = (
    "def _id_filter(id):\n"
    "    from bson import ObjectId\n"
    "    if ObjectId.is_valid(id):\n"
    "        return {'_id': {'$in': [id, ObjectId(id)]}}\n"
    "    return {'_id': id}\n"
)


def materialize_module_write_implementations(
    files: Mapping[str, str], *, app_build_plan: Any, data_contract: Any = None,
    owned_paths: list[str] | set[str] | None = None, subscription_contract: Any = None,
) -> dict[str, str]:
    """Compile canonical write functions within task-owned handler/service/repo paths.

    Supply admitted module manifests alongside the task's candidate sources.
    Scope ``owned_paths`` before task ownership validation; omit it at assembly.
    Authored hooks and schema imports are validated before any replacement.
    Model-authored code around the rendered functions is then normalized:
    ``ctx.emit`` literals point at declared events and repository code no
    business logic references is removed (see ``module_authored_code``).
    """
    plan = detach(app_build_plan)
    if not isinstance(plan, dict):
        return {}
    contract = detach(data_contract)
    aliases = canonical_write_event_aliases(contract)
    changed: dict[str, str] = {}
    allowed = set(owned_paths) if owned_paths is not None else None
    for path, content in files.items():
        match = re.fullmatch(r"modules/([^/]+)/module\.yaml", path)
        if not match:
            continue
        module_id = match[1]
        if not _owned_collections(module_id, plan, contract):
            continue
        manifest = yaml.safe_load(content)
        collections = _write_collections(module_id, plan, contract)
        actions = manifest.get("actions") or []
        declared = {action["id"] for action in actions}
        emits = {action["id"]: list(action.get("emits") or []) for action in actions}
        read_ids = _canonical_read_ids(module_id, plan, contract)
        functions: list[dict[str, str]] = [{}, {}, {}]
        write_events: dict[str, str] = {}
        canonical_events: set[str] = set()
        if collections:
            rendered_schemas = render_module_schemas(module_id, _owned_collections(module_id, plan, contract))
            _reject_stale_schema_imports(module_id, files, rendered_schemas)
            identifiers = [entity_identifier(collection["entity"]) for collection in collections]
            service_source = files.get(f"modules/{module_id}/backend/service.py")
            if service_source:
                validate_write_hooks(f"modules/{module_id}/backend/service.py", service_source, identifiers)
        for collection in collections:
            if collection["tenancy"] == "app_wide" and _protected_siblings(
                actions, read_ids, module_id=module_id, subscription_contract=subscription_contract,
            ):
                continue  # Explicitly declared protected app-wide writes keep their authored implementation.
            shape = collection_record_shape(module_id, collection)
            if shape["id_field"] == "_id":
                functions[2]["_id_filter"] = _ID_FILTER
            functions[2][f"load_{shape['identifier']}"] = _load_function(module_id, shape)
            for operation in CANONICAL_WRITE_OPERATIONS:
                method = canonical_write_action_id(shape["entity"], operation)
                if method not in declared:
                    raise ValueError(f"{module_id}: construct {method!r} in module.yaml before its implementation")
                event_type = canonical_write_event_type(shape["entity"], operation)
                canonical_events.add(event_type)
                if event_type in emits[method]:
                    write_events[method] = event_type
                rendered = _write_functions(module_id, shape, operation, write_events.get(method))
                for layer, source in zip(functions, rendered, strict=True):
                    layer[method] = source
        sources = {
            name: files[name] for name in files
            if name.startswith(f"modules/{module_id}/backend/") and name.endswith(".py")
        }
        for index, filename in enumerate(("handler.py", "service.py", "repo.py")):
            target = f"modules/{module_id}/backend/{filename}"
            if not functions[index] or (allowed is not None and target not in allowed):
                continue
            class_name = None
            if filename == "handler.py":
                entrypoint = str(manifest.get("module", {}).get("handler") or "")
                if not re.fullmatch(r"backend\.handler:[A-Za-z_][A-Za-z0-9_]*", entrypoint):
                    raise ValueError(f"{module_id}: canonical writes require module.handler=backend.handler:ClassName")
                class_name = entrypoint.split(":", 1)[1]
            sources[target] = _replace_functions(
                files.get(target, ""), functions[index], path=target, class_name=class_name,
            )
        events_source = files.get(f"modules/{module_id}/contracts/events.yaml")
        events_document = yaml.safe_load(events_source) if events_source else None
        declared_events = {
            str(event.get("type")) for event in (events_document or {}).get("events") or [] if isinstance(event, dict)
        } | {str(event) for values in emits.values() for event in values}
        for filename in ("handler.py", "service.py"):
            target = f"modules/{module_id}/backend/{filename}"
            if target in sources and (allowed is None or target in allowed):
                sources[target] = reconcile_emit_literals(
                    target, sources[target], declared=declared_events, aliases=aliases, write_events=write_events,
                    canonical_events=canonical_events,
                )
        repo_path = f"modules/{module_id}/backend/repo.py"
        if repo_path in sources and (allowed is None or repo_path in allowed):
            sources[repo_path] = prune_repository(
                repo_path, sources[repo_path], module_id=module_id,
                code_owned=set(functions[2]) | read_ids,
                business_sources={name: text for name, text in sources.items() if name != repo_path},
            )
        for target, source in sources.items():
            if source != files.get(target, "") and (allowed is None or target in allowed):
                changed[target] = source
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
    "code_owned_schema_paths",
    "collection_record_shape",
    "auth_contract_scopes",
    "materialize_module_actions",
    "materialize_module_schemas",
    "materialize_module_write_implementations",
    "materialize_task_module_schemas",
    "render_module_schemas",
    "rendered_schema_names",
    "validate_write_hooks",
]
