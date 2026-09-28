"""Canonical data contract field types, defaults and record-id rules.

DesignDocs validates these at save time, where the agent that can change the
contract receives the feedback. AppGenerator repeats the same checks only as a
backstop before rendering canonical writes.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from mozaiksai.core.workflow.generator_support.module_action_inventory import entity_identifier

# One canonical logical type per storage shape. The DesignDocs structured output
# literal, the prompt and this list must agree; tests hold them together.
CANONICAL_FIELD_TYPES: tuple[str, ...] = (
    "string", "boolean", "integer", "number", "date", "datetime", "object", "array",
)
STRUCTURED_FIELD_TYPES = frozenset({"object", "array"})
DATE_FIELD_TYPES = frozenset({"date", "datetime"})

_PYTHON_KINDS: dict[str, tuple[type, ...]] = {
    "string": (str,), "boolean": (bool,), "integer": (int,), "number": (int, float),
    "date": (str,), "datetime": (str,), "object": (dict,), "array": (list,),
}


class DataContractFieldError(ValueError):
    """A collection field the design agent must revise; the message lists valid choices."""


def field_type(field: Mapping[str, Any], location: str) -> str:
    kind = field.get("type")
    if not isinstance(kind, str) or kind not in CANONICAL_FIELD_TYPES:
        raise DataContractFieldError(
            f"{location}: field {field.get('name')!r} type {kind!r} is not a canonical contract type; "
            f"valid choices={list(CANONICAL_FIELD_TYPES)}"
        )
    return kind


def parse_default(field: Mapping[str, Any], location: str) -> tuple[bool, Any]:
    """Decode a JSON-encoded default and require it to match the declared type.

    A string field accepts a bare string that is not JSON as itself. ``"null"``
    is never a default: it would silently drop a requirement.
    """
    raw = field.get("default")
    if raw is None:
        return False, None
    kind = field_type(field, location)
    name = field.get("name")
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            if kind == "string":
                return True, raw
            raise DataContractFieldError(
                f"{location}: field {name!r} default {raw!r} is not JSON-encoded {kind}; "
                f"valid examples={_examples(kind)}"
            ) from None
    else:
        value = raw
    if value is None:
        raise DataContractFieldError(
            f"{location}: field {name!r} default 'null' is not allowed; omit default and set required=false "
            "or nullable=true instead"
        )
    expected = _PYTHON_KINDS[kind]
    if isinstance(value, bool) and kind not in {"boolean"}:
        matches = False
    else:
        matches = isinstance(value, expected)
    if not matches:
        raise DataContractFieldError(
            f"{location}: field {name!r} default {raw!r} does not match declared type {kind!r}; "
            f"valid examples={_examples(kind)}"
        )
    if field.get("enum") and value not in field["enum"]:
        raise DataContractFieldError(
            f"{location}: field {name!r} default {raw!r} must be one of enum {list(field['enum'])!r}"
        )
    return True, value


def _examples(kind: str) -> list[str]:
    return {
        "string": ['"pending"', "pending"], "boolean": ["true", "false"], "integer": ["0", "42"],
        "number": ["0", "1.5"], "date": ['"2026-01-01"'], "datetime": ['"2026-01-01T00:00:00Z"'],
        "object": ["{}", '{"key": "value"}'], "array": ["[]", '["a"]'],
    }[kind]


def record_id_field(collection: Mapping[str, Any], names: list[str]) -> str:
    """The generated record id is ``<entity>_id`` when declared, else ``id``, else ``_id``.

    ``search_by`` is a lookup key and may be a user-entered natural key; it is
    never overwritten with a generated id. The owner field is stamped by the
    runtime, so it can never be the generated id either.
    """
    identifier = entity_identifier(str(collection.get("entity") or ""))
    owner_field = collection.get("owner_field")
    for candidate in (f"{identifier}_id", "id"):
        if candidate in names and candidate != owner_field:
            return candidate
    return "_id"


def normalize_structured_defaults(collection: Mapping[str, Any], location: str) -> list[str]:
    """Give a required array/object field without a default its empty default.

    The correction is determined: canonical create input cannot carry structured
    values, so the record starts empty and hooks or custom mutations fill it.
    Unknown types are left for validation to reject.
    """
    normalized: list[str] = []
    for field in collection.get("fields") or []:
        if (
            isinstance(field, dict) and field.get("type") in STRUCTURED_FIELD_TYPES
            and field.get("required") and field.get("default") is None
        ):
            field["default"] = "[]" if field["type"] == "array" else "{}"
            normalized.append(f"{location} field {field.get('name')!r}: required {field['type']} default -> {field['default']}")
    return normalized


def validate_collection_fields(collection: Mapping[str, Any], location: str) -> None:
    """Reject field shapes canonical writes cannot carry, naming the valid choices."""
    fields = [field for field in collection.get("fields") or [] if isinstance(field, Mapping)]
    names = [str(field.get("name") or "") for field in fields]
    if len(names) != len(set(names)) or any(not name for name in names):
        raise DataContractFieldError(f"{location}: field names must be nonempty and unique")
    for field in fields:
        kind = field_type(field, location)
        has_default, _value = parse_default(field, location)
        if field.get("enum") is not None and (
            not isinstance(field["enum"], list) or not field["enum"]
            or any(not isinstance(item, str) for item in field["enum"])
        ):
            raise DataContractFieldError(
                f"{location}: field {field.get('name')!r} enum must be a nonempty list of strings or null"
            )
        if field.get("enum") and kind != "string":
            raise DataContractFieldError(
                f"{location}: field {field.get('name')!r} enum requires type 'string', got {kind!r}"
            )
        if kind in STRUCTURED_FIELD_TYPES and field.get("required") and not has_default:
            raise DataContractFieldError(
                f"{location}: required field {field.get('name')!r} has structured type {kind!r}, which "
                "canonical create input cannot carry; declare a JSON default "
                f"(valid examples={_examples(kind)}), or set required=false"
            )
    search_by = collection.get("search_by")
    if search_by is not None and search_by not in names:
        raise DataContractFieldError(
            f"{location}: search_by {search_by!r} must name a declared field or be null; valid choices={names}"
        )


__all__ = [
    "CANONICAL_FIELD_TYPES",
    "DATE_FIELD_TYPES",
    "DataContractFieldError",
    "STRUCTURED_FIELD_TYPES",
    "field_type",
    "normalize_structured_defaults",
    "parse_default",
    "record_id_field",
    "validate_collection_fields",
]
