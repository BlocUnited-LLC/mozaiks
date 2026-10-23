"""Runtime structured-output validation for workflow agent replies."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin

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


def _unwrap(annotation: Any) -> Any:
    """Strip ``Annotated`` and ``Optional``; ``None`` when the shape is ambiguous."""

    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            annotation = get_args(annotation)[0]
        elif origin is Union or origin is UnionType:
            options = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(options) != 1:
                return None
            annotation = options[0]
        else:
            return annotation


def _step(annotation: Any, segment: Any) -> tuple[str, Any]:
    """One validation-path segment: the text safe to record, and the next type.

    Only what the schema itself proves is kept: a declared field of a model, or
    an index into a list or tuple. Mapping keys, undeclared keys, union tags and
    anything on an unrecognised shape are model-controlled text and become the
    sentinel; ``None`` as the next type makes every later segment a sentinel too.
    """

    annotation = _unwrap(annotation)
    origin, args = get_origin(annotation), get_args(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation.__pydantic_root_model__ or not isinstance(segment, str):
            return _REDACTED_FIELD, None
        matches = [
            info
            for name, info in annotation.model_fields.items()
            if segment == name or segment == info.alias or segment == info.validation_alias
        ]
        if len(matches) == 1:
            return segment, matches[0].annotation
        return _REDACTED_FIELD, None
    if origin in (dict, Mapping) and len(args) == 2:
        return _REDACTED_FIELD, args[1]
    if origin in (list, set, frozenset, Sequence) and args:
        element = args[0]
    elif origin is tuple and args:
        position = segment if isinstance(segment, int) and not isinstance(segment, bool) else -1
        if args[-1] is Ellipsis:
            element = args[0]
        elif 0 <= position < len(args):
            element = args[position]
        else:
            return _REDACTED_FIELD, None
    else:
        return _REDACTED_FIELD, None
    if isinstance(segment, int) and not isinstance(segment, bool) and segment >= 0:
        return str(segment), element
    return _REDACTED_FIELD, None


def _failure_fields(exc: ValidationError, model_cls: type[BaseModel]) -> tuple[str, ...]:
    """Bounded ``<schema-proven path>:<error type>`` pairs; never input values."""

    fields: list[str] = []
    for item in exc.errors(include_input=False, include_context=False, include_url=False):
        parts: list[str] = []
        annotation: Any = model_cls
        for segment in item.get("loc", ()):
            label, annotation = _step(annotation, segment)
            parts.append(label)
        error_type = str(item.get("type", ""))
        if parts and error_type in {"extra_forbidden", "invalid_key"}:
            # The final segment is the offending key itself, whatever it is named.
            parts[-1] = _REDACTED_FIELD
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
                _failure_fields(exc, model_cls)
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
