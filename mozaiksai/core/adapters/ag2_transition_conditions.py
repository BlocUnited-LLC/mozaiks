"""Mozaiks transition-condition adapters for AG2 beta Network."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from autogen.agentchat.group import ContextExpression
from autogen.beta.network import (
    ContextEquals,
    Envelope,
    FromSpeaker,
    ToolCalled,
    WorkflowState,
    register_condition,
)


@dataclass(frozen=True)
class _MappingContextVariables:
    values: Mapping[str, Any]

    def contains(self, key: str) -> bool:
        return key in self.values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


@dataclass(slots=True)
class SourceScopedContextEquals:
    """Source-scoped adapter over AG2's `ContextEquals` condition."""

    source_agent_id: str
    key: str
    value: Any
    name: ClassVar[str] = "mozaiks_source_context_equals"

    def evaluate(self, state: WorkflowState, envelope: Envelope) -> bool:
        return FromSpeaker(self.source_agent_id).evaluate(state, envelope) and ContextEquals(
            key=self.key,
            value=self.value,
        ).evaluate(state, envelope)


@dataclass
class SourceScopedContextExpression:
    """Source-scoped adapter over AG2's `ContextExpression` evaluator."""

    source_agent_id: str
    expression: str
    name: ClassVar[str] = "mozaiks_source_context_expression"

    def __post_init__(self) -> None:
        self._compiled_expression = ContextExpression(self.expression)

    def evaluate(self, state: WorkflowState, envelope: Envelope) -> bool:
        return FromSpeaker(self.source_agent_id).evaluate(state, envelope) and bool(
            self._compiled_expression.evaluate(_MappingContextVariables(state.context_vars))  # type: ignore[arg-type]
        )


@dataclass(slots=True)
class SourceScopedToolCalled:
    """Source-scoped adapter over AG2's `ToolCalled` condition."""

    source_agent_id: str
    tool_name: str
    name: ClassVar[str] = "mozaiks_source_tool_called"

    def evaluate(self, state: WorkflowState, envelope: Envelope) -> bool:
        return FromSpeaker(self.source_agent_id).evaluate(state, envelope) and ToolCalled(
            tool_name=self.tool_name,
        ).evaluate(state, envelope)


register_condition(SourceScopedContextEquals)
register_condition(SourceScopedContextExpression)
register_condition(SourceScopedToolCalled)


__all__ = [
    "SourceScopedContextEquals",
    "SourceScopedContextExpression",
    "SourceScopedToolCalled",
]
