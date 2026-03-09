# ==============================================================================
# FILE: core/workflow/tool_validation.py
# DESCRIPTION: Shared helpers to validate tool calls against structured output
#              models and emit standardized sentinel payloads when validation
#              fails. Designed to keep the runtime AG2-native while enforcing
#              modular JSON contracts defined in structured_outputs.json.
# ==============================================================================
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from pydantic import BaseModel, ValidationError

from logs.logging_config import get_workflow_logger
from ..outputs.structured import get_structured_outputs_for_workflow

# ---------------------------------------------------------------------------
# Sentinel constants (ensure a single source of truth across runtime layers)
# ---------------------------------------------------------------------------
SENTINEL_FLAG = "_schema_validation_failed"
SENTINEL_STATUS = "schema_validation_error"
SENTINEL_MESSAGE_KEY = "message"
SENTINEL_ERRORS_KEY = "errors"
SENTINEL_EXPECTED_MODEL_KEY = "expected_model"
SENTINEL_AGENT_KEY = "agent_name"
SENTINEL_TOOL_KEY = "tool_name"

# Runtime keys injected by AG2 / orchestrator that should be ignored when
# validating tool payloads (never part of the schema).
RUNTIME_ARG_KEYS = {"context_variables", "runtime", "self"}


@dataclass(frozen=True)
class ValidationOutcome:
    """Structured representation of schema validation results."""

    is_valid: bool
    normalized_payload: Optional[Dict[str, Any]] = None
    error_payload: Optional[Dict[str, Any]] = None


def _filter_payload(payload: Dict[str, Any], model_cls: type[BaseModel]) -> Dict[str, Any]:
    """Drop runtime/system keys before validation to avoid false positives."""

    if not hasattr(model_cls, "model_fields"):
        return {k: v for k, v in payload.items() if k not in RUNTIME_ARG_KEYS}
    field_names = set(model_cls.model_fields.keys())  # type: ignore[attr-defined]
    filtered: Dict[str, Any] = {}
    for key, value in payload.items():
        if key in RUNTIME_ARG_KEYS:
            continue
        if field_names and key not in field_names:
            continue
        filtered[key] = value
    return filtered


def _build_error_payload(
    *,
    agent_name: str,
    tool_name: str,
    model_cls: type[BaseModel],
    validation_error: ValidationError,
) -> Dict[str, Any]:
    """Normalize ValidationError into a deterministic sentinel payload."""

    error_entries: Iterable[Dict[str, Any]] = validation_error.errors()
    logger = get_workflow_logger(workflow_name="tool_validation")
    logger.warning(
        "[TOOL_VALIDATION] Schema validation failed",
        extra={
            "agent": agent_name,
            "tool": tool_name,
            "model": getattr(model_cls, "__name__", "unknown"),
            "errors": list(error_entries),
        },
    )

    return {
        SENTINEL_FLAG: True,
        "status": SENTINEL_STATUS,
        SENTINEL_MESSAGE_KEY: (
            f"Tool '{tool_name}' payload failed schema validation for model "
            f"{getattr(model_cls, '__name__', 'unknown')}"
        ),
        SENTINEL_ERRORS_KEY: list(error_entries),
        SENTINEL_EXPECTED_MODEL_KEY: getattr(model_cls, "__name__", "unknown"),
        SENTINEL_AGENT_KEY: agent_name,
        SENTINEL_TOOL_KEY: tool_name,
    }


def validate_tool_call(
    *,
    workflow_name: str,
    agent_name: str,
    tool_name: str,
    raw_payload: Dict[str, Any],
) -> ValidationOutcome:
    """Validate a tool call payload against the workflow's structured outputs."""

    registry = get_structured_outputs_for_workflow(workflow_name)
    model_cls = registry.get(agent_name)

    if model_cls is None:
        return ValidationOutcome(is_valid=True, normalized_payload=raw_payload)

    filtered_payload = _filter_payload(raw_payload, model_cls)

    try:
        model_instance = model_cls.model_validate(filtered_payload)
    except ValidationError as err:
        return ValidationOutcome(
            is_valid=False,
            error_payload=_build_error_payload(
                agent_name=agent_name,
                tool_name=tool_name,
                model_cls=model_cls,
                validation_error=err,
            ),
        )

    normalized = None
    try:
        normalized = model_instance.model_dump()  # type: ignore[attr-defined]
    except Exception:
        normalized = filtered_payload

    return ValidationOutcome(is_valid=True, normalized_payload=normalized)


__all__ = [
    "ValidationOutcome",
    "validate_tool_call",
    "SENTINEL_FLAG",
    "SENTINEL_STATUS",
    "SENTINEL_MESSAGE_KEY",
    "SENTINEL_ERRORS_KEY",
    "SENTINEL_EXPECTED_MODEL_KEY",
    "SENTINEL_AGENT_KEY",
    "SENTINEL_TOOL_KEY",
]

