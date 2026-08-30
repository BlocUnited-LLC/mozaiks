from __future__ import annotations

"""Shared JSON Schema helpers for runtime contract validation."""

from dataclasses import dataclass
from typing import Any

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "path": self.path,
            "schema_path": self.schema_path,
            "validator": self.validator,
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


def validate_json_schema(value: Any, schema: dict[str, Any]) -> SchemaValidationDiagnostic | None:
    """Validate *value* against a JSON Schema dict and return structured errors."""

    if not schema or not isinstance(schema, dict):
        return None
    try:
        validator = jsonschema.Draft7Validator(normalize_nullable_schema(schema))
        errors = sorted(
            validator.iter_errors(value),
            key=lambda err: (list(err.path), list(err.schema_path), err.message),
        )
        if not errors:
            return None
        exc = errors[0]
        return SchemaValidationDiagnostic(
            message=exc.message,
            path=_json_path(exc.path),
            schema_path=_json_path(exc.schema_path),
            validator=str(exc.validator) if exc.validator else None,
        )
    except Exception as exc:  # malformed schema - preserve existing non-crashing behavior
        logger.warning("MODULE_SCHEMA_ERROR: could not validate schema: %s", exc)
        return None


def _json_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif isinstance(part, str) and part.isidentifier():
            path += f".{part}"
        else:
            path += f"[{part!r}]"
    return path
