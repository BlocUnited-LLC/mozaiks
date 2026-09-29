"""Canonical data contract field types, defaults and record-id rules.

DesignDocs validates these at save time, where the agent that can change the
contract receives the feedback. AppGenerator repeats the same checks only as a
backstop before rendering canonical writes.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from mozaiksai.core.workflow.generator_support.module_action_inventory import entity_identifier

# One canonical logical type per storage shape. The DesignDocs structured output
# literal, the prompt and this list must agree; tests hold them together.
CANONICAL_FIELD_TYPES: tuple[str, ...] = (
    "string", "boolean", "integer", "number", "date", "datetime", "object", "array",
)
STRUCTURED_FIELD_TYPES = frozenset({"object", "array"})
DATE_FIELD_TYPES = frozenset({"date", "datetime"})
# Record timestamps canonical writes stamp when declared as date or datetime.
MANAGED_TIMESTAMP_FIELDS = frozenset({"created_at", "updated_at"})
# Words that mean exactly one boolean value, however they are cased.
_BOOLEAN_WORDS = {"true": "true", "false": "false", "yes": "true", "no": "false"}

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
                f"valid examples={_examples(kind)} or null"
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
            f"valid examples={_examples(kind)} or null"
        )
    if field.get("enum") and value not in field["enum"]:
        raise DataContractFieldError(
            f"{location}: field {name!r} default {raw!r} must be one of enum {list(field['enum'])!r} or null"
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


def _determined_encoding(kind: Any, raw: Any) -> str | None:
    """The JSON a default that fails to decode unambiguously means, if it means exactly one value.

    A boolean word on a boolean ('True', 'FALSE', 'yes', 'No'), an integral
    number such as '3.0' on an integer, and a bare ISO date or datetime on a
    date/datetime field ('2026-01-01').
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if kind == "boolean":
        # A JSON-quoted word ('"yes"') is the same word.
        word = text[1:-1].strip() if len(text) > 1 and text[0] == text[-1] == '"' else text
        return _BOOLEAN_WORDS.get(word.casefold())
    if kind == "integer":
        try:
            number = float(text)
        except ValueError:
            return None
        return str(int(number)) if number.is_integer() else None
    if kind in DATE_FIELD_TYPES:
        try:
            (date.fromisoformat if kind == "date" else datetime.fromisoformat)(text)
        except ValueError:
            return None
        return json.dumps(text)
    return None


def _as_string_default(raw: Any) -> str:
    """The clause naming the default a field needs once its type is 'string', or '' when it already fits."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except ValueError:
            return ""
        if isinstance(decoded, str):
            return ""
    return f" and default {json.dumps(raw if isinstance(raw, str) else json.dumps(raw))!r}"


def is_managed_timestamp(field: Mapping[str, Any]) -> bool:
    """A created_at/updated_at date or datetime: canonical writes stamp it, whatever its default."""
    return field.get("name") in MANAGED_TIMESTAMP_FIELDS and field.get("type") in DATE_FIELD_TYPES


def normalize_structured_defaults(collection: Mapping[str, Any], location: str) -> list[str]:
    """Apply the determined default corrections and describe each one.

    Each correction is determined by the contract, not by the value's wording:

    - a managed timestamp's default is never used, because canonical writes
      stamp the field, so it becomes null ('now' and 'current_timestamp' are
      the model saying exactly that);
    - '' on a non-string field declares no value, so it becomes null;
    - a non-string default that fails to decode but means exactly one value
      ('True' or 'yes' on a boolean, '3.0' on an integer, a bare ISO date) is
      encoded as that value, required or not;
    - an optional non-string field whose default does not decode to its type
      otherwise starts without a value, so the default becomes null;
    - a required array/object field without a default starts empty ("[]" or
      "{}"): canonical create input cannot carry structured values, so hooks
      or custom mutations fill it.

    A required scalar's bad default is left for validation to reject: the
    value a required field starts with is a design decision. Unknown types are
    left for validation to reject as well.
    """
    normalized: list[str] = []
    for field in collection.get("fields") or []:
        if not isinstance(field, dict):
            continue
        kind, raw, name = field.get("type"), field.get("default"), field.get("name")
        if raw is not None and is_managed_timestamp(field):
            field["default"] = None
            normalized.append(f"{location} field {name!r}: managed timestamp default {raw!r} -> null (canonical writes stamp it)")
        elif raw is not None and kind in CANONICAL_FIELD_TYPES and kind != "string" and not field.get("enum"):
            # A non-string field with an enum is left as written: validation names the enum first.
            if isinstance(raw, str) and not raw.strip():
                field["default"] = None
                normalized.append(f"{location} field {name!r}: empty {kind} default -> null")
            else:
                try:
                    parse_default(field, location)
                except DataContractFieldError:
                    encoded = _determined_encoding(kind, raw)
                    if encoded is not None:
                        field["default"] = encoded
                        normalized.append(f"{location} field {name!r}: {kind} default {raw!r} -> {encoded}")
                    elif not field.get("required"):
                        field["default"] = None
                        normalized.append(
                            f"{location} field {name!r}: optional {kind} default {raw!r} does not decode -> null"
                        )
        if kind in STRUCTURED_FIELD_TYPES and field.get("required") and field.get("default") is None:
            field["default"] = "[]" if kind == "array" else "{}"
            normalized.append(f"{location} field {name!r}: required {kind} default -> {field['default']}")
    return normalized


def validate_collection_fields(collection: Mapping[str, Any], location: str) -> None:
    """Reject field shapes canonical writes cannot carry, naming the valid choices."""
    fields = [field for field in collection.get("fields") or [] if isinstance(field, Mapping)]
    names = [str(field.get("name") or "") for field in fields]
    if len(names) != len(set(names)) or any(not name for name in names):
        raise DataContractFieldError(f"{location}: field names must be nonempty and unique")
    for field in fields:
        kind = field_type(field, location)
        # The enum's shape comes first: a non-string enum makes any default check against it moot.
        if field.get("enum") is not None and (
            not isinstance(field["enum"], list) or not field["enum"]
            or any(not isinstance(item, str) for item in field["enum"])
        ):
            raise DataContractFieldError(
                f"{location}: field {field.get('name')!r} enum must be a nonempty list of strings or null"
            )
        if field.get("enum") and kind != "string":
            raise DataContractFieldError(
                f"{location}: field {field.get('name')!r} enum requires type 'string', got {kind!r}: "
                f"set type 'string'{_as_string_default(field.get('default'))}, or set enum null to keep type {kind!r}"
            )
        has_default, _value = parse_default(field, location)
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
    "MANAGED_TIMESTAMP_FIELDS",
    "STRUCTURED_FIELD_TYPES",
    "field_type",
    "is_managed_timestamp",
    "normalize_structured_defaults",
    "parse_default",
    "record_id_field",
    "validate_collection_fields",
]
