"""Structured output helpers for tool and message processing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object string."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def enforce_required_fields(
    data: dict[str, Any],
    required_fields: Iterable[str],
) -> tuple[bool, list[str]]:
    """Validate required keys and return missing field names."""
    missing = [field for field in required_fields if field not in data]
    return (len(missing) == 0, missing)


@dataclass(slots=True, kw_only=True)
class StructuredValidationResult:
    valid: bool
    errors: list[str]
    normalized_output: dict[str, Any] | None = None


class StructuredOutputEnforcer:
    """Strict schema validator for deterministic structured outputs."""

    _TYPE_CHECKS = {
        "string": lambda value: isinstance(value, str),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
    }

    def validate(
        self,
        *,
        payload: dict[str, Any],
        schema: dict[str, Any],
    ) -> StructuredValidationResult:
        if not schema:
            return StructuredValidationResult(
                valid=True,
                errors=[],
                normalized_output=dict(payload),
            )

        errors: list[str] = []
        root_type = schema.get("type")
        if root_type and root_type != "object":
            errors.append("schema_root_type_must_be_object")
            return StructuredValidationResult(valid=False, errors=errors)

        required = schema.get("required", [])
        if isinstance(required, list):
            ok, missing = enforce_required_fields(payload, required)
            if not ok:
                for field in missing:
                    errors.append(f"missing_required_field:{field}")

        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, spec in properties.items():
                if key not in payload:
                    continue
                if not isinstance(spec, dict):
                    continue
                expected_type = spec.get("type")
                if expected_type is None:
                    continue
                checker = self._TYPE_CHECKS.get(str(expected_type))
                if checker is None:
                    errors.append(f"unsupported_type:{key}:{expected_type}")
                    continue
                if not checker(payload[key]):
                    errors.append(f"type_mismatch:{key}:{expected_type}")

        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            allowed_keys = set(properties.keys())
            unexpected = [key for key in payload if key not in allowed_keys]
            for key in unexpected:
                errors.append(f"unexpected_property:{key}")

        return StructuredValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            normalized_output=dict(payload) if len(errors) == 0 else None,
        )
