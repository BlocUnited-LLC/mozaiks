from __future__ import annotations

"""Shared JSON Schema helpers for runtime contract validation."""

from dataclasses import dataclass
from typing import Any, Literal

import jsonschema

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("runtime_schema_validation")


@dataclass(frozen=True)
class SchemaValidationDiagnostic:
    """Structured validation failure details for runtime diagnostics."""

    message: str
    path: str
    schema_path: str
    validator: str | None = None
    category: Literal["value_invalid", "schema_invalid"] = "value_invalid"

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "path": self.path,
            "schema_path": self.schema_path,
            "validator": self.validator,
            "category": self.category,
        }


def normalize_nullable_schema(schema: Any) -> Any:
    """Translate OpenAPI-style nullable fields into JSON Schema."""

    if isinstance(schema, list):
        return [normalize_nullable_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    normalized = {key: normalize_nullable_schema(value) for key, value in schema.items()}
    if normalized.get("nullable") is True:
        schema_type = normalized.get("type")
        if isinstance(schema_type, str):
            normalized["type"] = [schema_type, "null"] if schema_type != "null" else schema_type
        elif isinstance(schema_type, list) and "null" not in schema_type:
            normalized["type"] = [*schema_type, "null"]

        enum_values = normalized.get("enum")
        if isinstance(enum_values, list) and None not in enum_values:
            normalized["enum"] = [*enum_values, None]

    return normalized


def _schema_failure(
    message: str, *, validator: str, schema_path: str = "$",
) -> SchemaValidationDiagnostic:
    logger.warning("MODULE_SCHEMA_ERROR: %s schema_path=%s validator=%s", message, schema_path, validator)
    return SchemaValidationDiagnostic(
        message=message,
        path="$",
        schema_path=schema_path,
        validator=validator,
        category="schema_invalid",
    )


def validate_json_schema(
    value: Any, schema: dict[str, Any] | bool,
) -> SchemaValidationDiagnostic | None:
    """Check a normalized Draft 7 schema and then validate its value.

    ``None`` is returned only after both checks succeed. The empty object is
    a valid universal schema; absence is handled explicitly by callers, not
    represented as an unchecked schema here. Exception diagnostics use stable
    stage descriptions rather than exception reprs or runtime addresses.
    """
    try:
        normalized = normalize_nullable_schema(schema)
    except Exception:
        return _schema_failure("Schema normalization failed.", validator="normalization")
    try:
        jsonschema.Draft7Validator.check_schema(normalized)
    except jsonschema.exceptions.SchemaError as exc:
        return _schema_failure(
            "Schema is invalid under Draft 7.",
            validator=exc.validator if type(exc.validator) is str else "schema",
            schema_path=_json_path(exc.path),
        )
    except Exception:
        return _schema_failure("Schema checking failed.", validator="schema")
    try:
        validator = jsonschema.Draft7Validator(normalized)
    except Exception:
        return _schema_failure("Schema validator construction failed.", validator="construction")
    try:
        errors = sorted(
            validator.iter_errors(value),
            key=lambda err: (_json_path(err.path), _json_path(err.schema_path), str(err.validator)),
        )
        if not errors:
            return None
        value_error = errors[0]
        constraint = str(value_error.validator) if value_error.validator else None
        message = (f"Value does not satisfy the {constraint!r} constraint." if constraint
                   else "Value does not satisfy the declared schema.")
        if constraint == "required" and isinstance(value_error.instance, dict):
            missing = sorted(name for name in value_error.validator_value if name not in value_error.instance)
            message = "Missing required properties: " + ", ".join(repr(name) for name in missing) + "."
        return SchemaValidationDiagnostic(
            message=message,
            path=_json_path(value_error.path),
            schema_path=_json_path(value_error.schema_path),
            validator=constraint,
        )
    except Exception:
        return _schema_failure("Schema evaluation failed.", validator="evaluation")


def _json_path(parts: Any) -> str:
    path = "$"
    try:
        for part in parts:
            if type(part) is int:
                path += f"[{part}]"
            elif type(part) is str:
                path += f".{part}" if part.isidentifier() else f"[{part!r}]"
            else:
                path += '["<non-json-key>"]'
    except Exception:
        return '$["<invalid-path>"]'
    return path
