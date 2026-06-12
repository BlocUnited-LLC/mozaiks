"""Runtime structured-output validation for workflow agent replies."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(slots=True)
class StructuredOutputValidation:
    """Result of validating one agent reply against a structured-output model."""

    agent_name: str
    model_name: str
    raw_data: dict[str, Any]
    structured_data: dict[str, Any] | None
    validation_passed: bool
    error: str | None = None


def reply_body_to_data(reply: Any) -> Any:
    """Normalize an AG2 reply, envelope body, dict, list, or Pydantic model."""

    body = getattr(reply, "body", reply)
    if isinstance(body, BaseModel):
        return body.model_dump(mode="json")
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, str):
        text = body.strip()
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
    "reply_body_to_data",
    "validate_agent_structured_output",
]
