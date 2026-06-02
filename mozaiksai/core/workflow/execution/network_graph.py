"""Compile Mozaiks workflow routing into AG2 beta Network transition graphs."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, ClassVar, Mapping, Sequence

from autogen.beta.network import (
    EV_TEXT,
    AgentTarget,
    Envelope,
    FromSpeaker,
    TerminateTarget,
    Transition,
    TransitionGraph,
    WorkflowState,
    register_condition,
)

_SPECIAL_TERMINATE = frozenset({"terminate", "end", "stop"})
_SPECIAL_USER = frozenset({"user", "user_proxy", "userproxy", "userproxyagent"})
_SUPPORTED_CONDITION_TYPES = frozenset({"expression", "context_expression", "context"})

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BOOL_NULL_REPLACEMENTS = (
    (re.compile(r"\btrue\b", re.IGNORECASE), "True"),
    (re.compile(r"\bfalse\b", re.IGNORECASE), "False"),
    (re.compile(r"\bnull\b", re.IGNORECASE), "None"),
)


class WorkflowGraphCompileError(ValueError):
    """Raised when a Mozaiks handoff contract cannot compile to AG2 Network."""


@dataclass(slots=True)
class MozaiksContextExpression:
    """AG2 transition condition for Mozaiks `${context_key}` expressions.

    The expression is deliberately local and deterministic. Workflow handoffs do
    not perform LLM classification at routing time; LLM-driven intent routing
    belongs in the control plane before a workflow run is started or resumed.
    """

    source_agent_id: str
    expression: str
    name: ClassVar[str] = "mozaiks_context_expression"

    def evaluate(self, state: WorkflowState, envelope: Envelope) -> bool:
        if envelope.sender_id != self.source_agent_id:
            return False
        return evaluate_context_expression(self.expression, state.context_vars)


register_condition(MozaiksContextExpression)


def compile_handoffs_to_transition_graph(
    handoff_rules: Sequence[Mapping[str, Any]],
    *,
    initial_agent_name: str,
    agent_id_by_name: Mapping[str, str] | None = None,
    max_turns: int | None = None,
) -> TransitionGraph:
    """Compile validated `handoffs.yaml` rules into an AG2 `TransitionGraph`."""

    agent_ids = {str(k): str(v) for k, v in dict(agent_id_by_name or {}).items()}
    agent_ids.setdefault("user", "user")

    transitions: list[Transition] = []
    for index, raw_rule in enumerate(handoff_rules or []):
        rule = dict(raw_rule)
        source_name = _required_rule_text(rule, "source_agent")
        target_name = _required_rule_text(rule, "target_agent")
        handoff_type = str(rule.get("handoff_type") or "after_work").strip().lower()
        condition = _optional_text(rule.get("condition"))
        condition_type = _optional_text(rule.get("condition_type"))
        normalized_condition_type = str(condition_type or "").strip().lower()

        if normalized_condition_type in {"llm", "string_llm"}:
            raise WorkflowGraphCompileError(
                f"handoff rule {source_name!r} -> {target_name!r} uses "
                f"condition_type={condition_type!r}; AG2 Network workflow "
                "routing is deterministic. Move this intent classification to "
                "the control plane or set a context variable/tool result first."
            )

        source_id = _agent_id(source_name, agent_ids)
        target = _target_for(target_name, agent_ids, rule.get("transition_target"))

        if handoff_type == "after_work" and not condition:
            when = FromSpeaker(source_id)
        elif handoff_type == "condition" or condition:
            if not condition:
                raise WorkflowGraphCompileError(
                    f"handoff rule {source_name!r} -> {target_name!r} requires a condition"
                )
            if normalized_condition_type and normalized_condition_type not in _SUPPORTED_CONDITION_TYPES:
                raise WorkflowGraphCompileError(
                    f"handoff rule {source_name!r} -> {target_name!r} uses unsupported "
                    f"condition_type={condition_type!r}"
                )
            when = MozaiksContextExpression(source_agent_id=source_id, expression=condition)
        else:
            raise WorkflowGraphCompileError(
                f"handoff rule {source_name!r} -> {target_name!r} uses unsupported "
                f"handoff_type={handoff_type!r}"
            )

        transitions.append(Transition(when=when, then=target, priority=index))

    initial_speaker = _agent_id(initial_agent_name, agent_ids)
    return TransitionGraph(
        initial_speaker=initial_speaker,
        transitions=transitions,
        default_target=TerminateTarget(reason="no_handoff_matched"),
        max_turns=max_turns,
    )


def resolve_next_agent(
    graph: TransitionGraph,
    *,
    current_agent_name: str,
    context_variables: Mapping[str, Any],
    agent_name_by_id: Mapping[str, str] | None = None,
    participant_order: Sequence[str] | None = None,
    turn_count: int = 1,
) -> str | None:
    """Resolve the next workflow speaker from an AG2 `TransitionGraph`.

    Returns an agent name, `"user"` for a pause boundary, or `"terminate"`.
    This keeps the current Mozaiks run loop aligned with AG2 Network routing
    while the execution adapter owns event, persistence, and UI integration.
    """

    names_by_id = {str(k): str(v) for k, v in dict(agent_name_by_id or {}).items()}
    ids_by_name = {name: agent_id for agent_id, name in names_by_id.items()}
    current_id = ids_by_name.get(current_agent_name, current_agent_name)
    order = [str(item) for item in (participant_order or names_by_id.keys() or [current_id])]
    if current_id not in order:
        order.append(current_id)

    state = WorkflowState(
        participant_order=order,
        expected_next_speaker=current_id,
        last_speaker_id=current_id,
        turn_count=turn_count,
        creator_id="user",
        graph_data=graph.to_dict(),
        context_vars=dict(context_variables or {}),
    )
    envelope = Envelope(
        channel_id="mozaiks-local-routing",
        sender_id=current_id,
        audience=None,
        event_type=EV_TEXT,
        event_data={"text": ""},
    )

    for transition in sorted(graph.transitions, key=lambda item: item.priority):
        if transition.when.evaluate(state, envelope):
            decision = transition.then.resolve(state, envelope)
            return _route_name(decision.next_speaker, names_by_id)

    decision = graph.default_target.resolve(state, envelope)
    return _route_name(decision.next_speaker, names_by_id)


def evaluate_context_expression(expression: str, context_variables: Mapping[str, Any]) -> bool:
    """Evaluate the deterministic subset used by workflow handoff expressions."""

    rewritten, values = _rewrite_expression(expression, context_variables)
    parsed = ast.parse(rewritten, mode="eval")
    _validate_expression_ast(parsed)
    return bool(eval(compile(parsed, "<mozaiks-handoff-expression>", "eval"), {"__builtins__": {}}, values))  # noqa: S307


def _rewrite_expression(expression: str, context_variables: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    values: dict[str, Any] = {}

    def replace_var(match: re.Match[str]) -> str:
        key = match.group(1)
        safe_name = f"ctx_{key}"
        values[safe_name] = context_variables.get(key)
        return safe_name

    rewritten = _VAR_PATTERN.sub(replace_var, expression)
    for pattern, replacement in _BOOL_NULL_REPLACEMENTS:
        rewritten = pattern.sub(replacement, rewritten)
    return rewritten, values


def _validate_expression_ast(node: ast.AST) -> None:
    allowed = (
        ast.Expression,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.UnaryOp,
        ast.Not,
        ast.Compare,
        ast.Eq,
        ast.NotEq,
        ast.In,
        ast.NotIn,
        ast.Is,
        ast.IsNot,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.List,
        ast.Tuple,
    )
    for child in ast.walk(node):
        if not isinstance(child, allowed):
            raise WorkflowGraphCompileError(
                f"Unsupported handoff expression node: {type(child).__name__}"
            )


def _target_for(target_name: str, agent_ids: Mapping[str, str], transition_target: Any) -> Any:
    normalized_target = target_name.strip().lower()
    declared_target = str(transition_target or "").strip().lower()
    if normalized_target in _SPECIAL_TERMINATE or declared_target == "terminatetarget":
        return TerminateTarget(reason="workflow_complete")
    return AgentTarget(_agent_id(target_name, agent_ids))


def _route_name(agent_id: str | None, agent_name_by_id: Mapping[str, str]) -> str | None:
    if agent_id is None:
        return "terminate"
    if agent_id.lower() in _SPECIAL_USER:
        return "user"
    return agent_name_by_id.get(agent_id, agent_id)


def _agent_id(name: str, agent_ids: Mapping[str, str]) -> str:
    normalized = name.strip()
    if normalized.lower() in _SPECIAL_USER:
        return "user"
    return agent_ids.get(normalized, normalized)


def _required_rule_text(rule: Mapping[str, Any], key: str) -> str:
    value = str(rule.get(key) or "").strip()
    if not value:
        raise WorkflowGraphCompileError(f"handoff rule is missing {key!r}: {dict(rule)!r}")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


__all__ = [
    "MozaiksContextExpression",
    "WorkflowGraphCompileError",
    "compile_handoffs_to_transition_graph",
    "evaluate_context_expression",
    "resolve_next_agent",
]
