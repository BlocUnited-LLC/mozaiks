"""Canonical generated write operations and client inputs for a collection."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

CANONICAL_WRITE_OPERATIONS = ("create", "update", "delete")
DATE_FIELD_TYPES = frozenset({"date", "datetime"})
MANAGED_TIMESTAMP_FIELDS = frozenset({"created_at", "updated_at"})
SCOPE_FIELDS = frozenset({"app_id", "tenant_id", "workspace_id", "user_id"})


class DataContractFieldError(ValueError):
    """A collection field or write declaration that needs revision."""


def entity_identifier(entity: str) -> str:
    """Project a declared entity name such as ProjectMilestone to project_milestone."""
    if not isinstance(entity, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", entity):
        raise DataContractFieldError(f"Canonical writes require an identifier-safe entity name, got {entity!r}.")
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", entity).lower()
    return re.sub(r"_+", "_", snake)


def canonical_write_action_id(entity: str, operation: str) -> str:
    """Return a canonical action id within the named collection's module."""
    if operation not in CANONICAL_WRITE_OPERATIONS:
        raise ValueError(f"Unsupported canonical write operation {operation!r}; choose create, update or delete.")
    return f"{operation}_{entity_identifier(entity)}"


def record_id_field(collection: Mapping[str, Any], names: list[str]) -> str:
    """The generated id is the declared entity id, then id, then Mongo _id."""
    identifier = entity_identifier(str(collection.get("entity") or ""))
    for candidate in (f"{identifier}_id", "id"):
        if candidate in names and candidate != collection.get("owner_field"):
            return candidate
    return "_id"


def is_managed_timestamp(field: Mapping[str, Any]) -> bool:
    return field.get("name") in MANAGED_TIMESTAMP_FIELDS and field.get("type") in DATE_FIELD_TYPES


def canonical_write_input_schema(
    operation: str, fields: Sequence[Mapping[str, Any]], allowed_fields: Collection[str], record_key: str,
) -> dict[str, Any]:
    """Build the closed client contract shared by the generator and the host."""
    if operation not in CANONICAL_WRITE_OPERATIONS:
        raise DataContractFieldError(f"Unknown canonical write operation {operation!r}")
    properties: dict[str, Any] = {}
    required: list[str] = []
    if operation != "create":
        properties[record_key] = {"type": "string"}
        required.append(record_key)
    for field in fields:
        name = field["name"]
        if name not in allowed_fields:
            continue
        kind = field["type"]
        prop = {"type": "string" if kind in DATE_FIELD_TYPES else kind}
        if field.get("enum"):
            prop["enum"] = list(field["enum"])
        properties[name] = prop
        if operation == "create" and field.get("required") and field.get("default") is None:
            required.append(name)
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def canonical_write_fields(collection: Mapping[str, Any]) -> dict[str, tuple[str, ...]] | None:
    """Return the declared writable fields per operation, or None when omitted."""
    lifecycle = collection.get("lifecycle")
    if not isinstance(lifecycle, Mapping) or lifecycle.get("module_writes") is None:
        return None
    location = f"collection {collection.get('name')!r} lifecycle.module_writes"
    entries = lifecycle["module_writes"]
    if lifecycle.get("write_mode") != "module_action" or not isinstance(entries, list):
        raise DataContractFieldError(f"{location} requires write_mode=module_action and a list of operations")
    fields = {f["name"]: f for f in collection.get("fields") or [] if isinstance(f, Mapping) and isinstance(f.get("name"), str)}
    reserved = {"_id", record_id_field(collection, list(fields)), collection.get("owner_field"), *SCOPE_FIELDS}
    writable = {
        name for name, field in fields.items()
        if name not in reserved and field.get("type") in {"string", "boolean", "integer", "number", *DATE_FIELD_TYPES}
        and not is_managed_timestamp(field)
    }
    operations: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {"operation", "fields"}:
            raise DataContractFieldError(f"{location} entries require exactly operation and fields")
        operation, names = entry["operation"], entry["fields"]
        if operation not in CANONICAL_WRITE_OPERATIONS or operation in operations:
            raise DataContractFieldError(f"{location} requires unique operations from {list(CANONICAL_WRITE_OPERATIONS)}, got {operation!r}")
        if not isinstance(names, list) or any(not isinstance(name, str) or name not in writable for name in names):
            raise DataContractFieldError(f"{location}.{operation} fields must be a list of declared writable fields from {sorted(writable)}")
        if len(set(names)) != len(names) or (operation == "delete" and names) or (operation == "update" and not names):
            raise DataContractFieldError(f"{location}.{operation} needs unique fields, at least one for update and none for delete")
        operations[operation] = tuple(names)
    if "create" in operations:
        required = {name for name in writable if fields[name].get("required") and fields[name].get("default") is None}
        missing = required.difference(operations["create"])
        if missing:
            raise DataContractFieldError(f"{location}.create omits required fields {sorted(missing)}; allow them or declare a default")
    return operations
