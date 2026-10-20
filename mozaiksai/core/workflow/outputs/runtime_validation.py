"""Runtime structured-output validation for workflow agent replies."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, get_args

from pydantic import BaseModel, ValidationError


@dataclass(slots=True)
class StructuredOutputValidation:
    """Result of validating one agent reply against a structured-output model."""

    agent_name: str
    model_name: str
    raw_data: dict[str, Any]
    structured_data: dict[str, Any] | None
    validation_passed: bool
    error: str | None = None
    # Audit-safe description of a rejection. Unlike ``error`` (Pydantic's text
    # embeds the offending input values), these never carry output content.
    failure_category: str | None = None
    failure_fields: tuple[str, ...] = ()
    output_digest: str | None = None


_MAX_FAILURE_FIELDS = 10
_REDACTED_FIELD = "<redacted-field>"
_SAFE_ERROR_TYPE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _output_digest(data: Any) -> str:
    """SHA-256 of the canonical JSON of ``data``; the content itself is not kept."""

    try:
        canonical = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        canonical = repr(data)
    return hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()


def _nested_models(annotation: Any) -> list[type[BaseModel]]:
    """Every BaseModel class mentioned anywhere inside a type annotation."""

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    models: list[type[BaseModel]] = []
    for arg in get_args(annotation):
        models.extend(_nested_models(arg))
    return models


@lru_cache(maxsize=256)
def declared_field_names(model_cls: type[BaseModel]) -> frozenset[str]:
    """Field names and aliases declared by ``model_cls`` and the models nested in it.

    This is the only vocabulary an audit record may quote from a validation
    path: it comes from the schema, never from the model's output.
    """

    names: set[str] = set()
    seen: set[type[BaseModel]] = set()
    pending = [model_cls]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for name, info in current.model_fields.items():
            names.add(name)
            for alias in (info.alias, info.validation_alias):
                if isinstance(alias, str):
                    names.add(alias)
            pending.extend(_nested_models(info.annotation))
    return frozenset(names)


def _failure_fields(exc: ValidationError, declared: frozenset[str]) -> tuple[str, ...]:
    """``<field path>:<error type>`` pairs, bounded and free of model-controlled text.

    A path segment survives only if it is a list index or a declared field
    name. Anything else (an undeclared key the model added, a key inside an open
    dict, a union tag) is replaced by a fixed sentinel rather than truncated.
    """

    fields: list[str] = []
    for item in exc.errors(include_input=False, include_context=False, include_url=False):
        parts = [
            str(part)
            if isinstance(part, int) and not isinstance(part, bool)
            else part
            if isinstance(part, str) and part in declared
            else _REDACTED_FIELD
            for part in item.get("loc", ())
        ]
        error_type = str(item.get("type", ""))
        if _SAFE_ERROR_TYPE.fullmatch(error_type) is None:
            error_type = "unknown"
        fields.append(f"{'.'.join(parts) or '<root>'}:{error_type}")
        if len(fields) >= _MAX_FAILURE_FIELDS:
            break
    return tuple(fields)


_JSON_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*(?P<body>.*?)\s*```\s*$",
    re.DOTALL,
)


def normalize_json_candidate_text(value: str) -> str:
    """Return JSON text from a raw or fenced JSON candidate."""

    text = str(value or "").strip()
    match = _JSON_FENCE_RE.match(text)
    if match:
        return match.group("body").strip()
    return text


def reply_body_to_data(reply: Any) -> Any:
    """Normalize an AG2 reply, envelope body, dict, list, or Pydantic model."""

    body = getattr(reply, "body", reply)
    if isinstance(body, BaseModel):
        return body.model_dump(mode="json")
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, str):
        text = normalize_json_candidate_text(body)
        if not text:
            return text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return body
    return body


def validate_agent_structured_output(
    *,
    agent_name: str,
    reply: Any,
    structured_registry: Mapping[str, Any],
) -> StructuredOutputValidation | None:
    """Validate an agent reply when the agent has a registered model.

    Returns `None` when the agent has no structured-output contract. A returned
    result always means the agent was contract-bound, with `validation_passed`
    describing whether the reply satisfied that contract.
    """

    model_cls = structured_registry.get(agent_name)
    if model_cls is None:
        return None

    model_name = getattr(model_cls, "__name__", str(model_cls))
    raw_data = reply_body_to_data(reply)
    if not isinstance(raw_data, dict):
        return StructuredOutputValidation(
            agent_name=agent_name,
            model_name=model_name,
            raw_data={},
            structured_data=None,
            validation_passed=False,
            error="structured output was not a JSON object",
            failure_category="not_json_object",
            output_digest=_output_digest(raw_data),
        )

    try:
        validated = model_cls.model_validate(raw_data)
    except Exception as exc:
        return StructuredOutputValidation(
            agent_name=agent_name,
            model_name=model_name,
            raw_data=dict(raw_data),
            structured_data=None,
            validation_passed=False,
            error=str(exc),
            failure_category="schema_violation"
            if isinstance(exc, ValidationError)
            else "validation_error",
            failure_fields=(
                _failure_fields(exc, declared_field_names(model_cls))
                if isinstance(exc, ValidationError)
                else ()
            ),
            output_digest=_output_digest(raw_data),
        )

    return StructuredOutputValidation(
        agent_name=agent_name,
        model_name=model_name,
        raw_data=dict(raw_data),
        structured_data=validated.model_dump(mode="json"),
        validation_passed=True,
    )


__all__ = [
    "StructuredOutputValidation",
    "normalize_json_candidate_text",
    "reply_body_to_data",
    "validate_agent_structured_output",
]
