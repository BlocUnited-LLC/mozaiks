"""Mozaiks transition-condition adapters for AG2 Network."""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from ag2.network import (
    ContextEquals,
    Envelope,
    FromSpeaker,
    ToolCalled,
    WorkflowState,
    register_condition,
)

_CONTEXT_REF_RE = re.compile(r"\$\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}")
_ALLOWED_EXPRESSION_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
)
_ALLOWED_EXPRESSION_CALLS = {"len", "__ctx"}


class _MozaiksExpression:
    """Evaluate Mozaiks `${var}` context expressions for workflow routing."""

    def __init__(self, expression: str) -> None:
        self.expression = _CONTEXT_REF_RE.sub(
            lambda match: f"__ctx({match.group(1)!r})",
            expression,
        )
        self._compiled = self._compile(self.expression)

    def _compile(self, expression: str) -> Any:
        tree = ast.parse(expression, mode="eval")
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_EXPRESSION_NODES):
                raise ValueError(f"unsupported context_expression syntax: {type(node).__name__}")
            if isinstance(node, ast.Name) and node.id not in _ALLOWED_EXPRESSION_CALLS:
                raise ValueError(f"unsupported context_expression name: {node.id}")
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_EXPRESSION_CALLS:
                    raise ValueError("unsupported context_expression function call")
                if node.keywords:
                    raise ValueError("context_expression function keywords are not supported")
        return compile(tree, "<context_expression>", "eval")

    def evaluate(self, context_variables: Any) -> bool:
        def _ctx(key: str) -> Any:
            if hasattr(context_variables, "get"):
                return context_variables.get(key)
            return None

        return bool(eval(self._compiled, {"__builtins__": {}}, {"__ctx": _ctx, "len": len}))


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
    """Source-scoped adapter over Mozaiks' deterministic context expression contract."""

    source_agent_id: str
    expression: str
    name: ClassVar[str] = "mozaiks_source_context_expression"

    def __post_init__(self) -> None:
        self._compiled_expression = _MozaiksExpression(self.expression)

    def evaluate(self, state: WorkflowState, envelope: Envelope) -> bool:
        return FromSpeaker(self.source_agent_id).evaluate(state, envelope) and bool(
            self._compiled_expression.evaluate(_MappingContextVariables(state.context_vars))  # type: ignore[arg-type]
        )


@dataclass(slots=True)
class SourceScopedToolCalled(ToolCalled):
    """Native tool-call condition with Mozaiks' declared source scope.

    AG2 recognizes ToolCalled subclasses when deriving routing from tool events.
    The source predicate is evaluated when its native graph selects the target.
    """

    source_agent_id: str
    name: ClassVar[str] = "mozaiks_source_tool_called"

    def evaluate(self, state: WorkflowState, envelope: Envelope) -> bool:
        return FromSpeaker(self.source_agent_id).evaluate(state, envelope) and ToolCalled.evaluate(
            self, state, envelope
        )


@dataclass(slots=True)
class BootstrapInitialDispatch:
    """Bootstrap-only AG2 condition for Mozaiks' injected initial dispatch."""

    source_agent_id: str
    name: ClassVar[str] = "mozaiks_bootstrap_initial_dispatch"

    def evaluate(self, state: WorkflowState, envelope: Envelope) -> bool:
        return FromSpeaker(self.source_agent_id).evaluate(state, envelope) and int(
            state.turn_count
        ) == 1


register_condition(SourceScopedContextEquals)
register_condition(SourceScopedContextExpression)
register_condition(SourceScopedToolCalled)
register_condition(BootstrapInitialDispatch)


__all__ = [
    "BootstrapInitialDispatch",
    "SourceScopedContextEquals",
    "SourceScopedContextExpression",
    "SourceScopedToolCalled",
]
