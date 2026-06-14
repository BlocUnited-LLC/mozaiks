"""Compile Mozaiks workflow routing into AG2 beta Network transition graphs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from autogen.beta.network import (
    EV_PACKET,
    AgentTarget,
    Envelope,
    FromSpeaker,
    TerminateTarget,
    Transition,
    TransitionGraph,
    WorkflowAdapter,
    WorkflowState,
)

from mozaiksai.core.adapters.ag2_transition_conditions import (
    SourceScopedContextEquals,
    SourceScopedContextExpression,
    SourceScopedToolCalled,
)

_SPECIAL_TERMINATE = frozenset({"terminate"})
_SPECIAL_USER = frozenset({"user", "user_proxy", "userproxy", "userproxyagent"})
_SUPPORTED_CONDITION_TYPES = frozenset({"context_equals", "context_expression", "tool_called"})


class WorkflowGraphCompileError(ValueError):
    """Raised when a Mozaiks transition contract cannot compile to AG2 Network."""


def compile_transition_rules_to_graph(
    transition_rules: Sequence[Mapping[str, Any]],
    *,
    initial_agent_name: str,
    agent_id_by_name: Mapping[str, str] | None = None,
    max_turns: int | None = None,
) -> TransitionGraph:
    """Compile validated `transition_graph.yaml` rules into an AG2 `TransitionGraph`."""

    agent_ids = {str(k): str(v) for k, v in dict(agent_id_by_name or {}).items()}
    agent_ids.setdefault("user", "user")

    transitions: list[Transition] = []
    for index, raw_rule in enumerate(transition_rules or []):
        rule = dict(raw_rule)
        source_name = _required_rule_text(rule, "source_agent")
        target_name = _required_rule_text(rule, "target_agent")
        transition_type = _required_rule_text(rule, "transition_type").lower()
        condition_type = _optional_text(rule.get("condition_type"))
        normalized_condition_type = str(condition_type or "").strip().lower()
        condition_key = _optional_text(rule.get("condition_key"))
        condition_value = rule.get("condition_value")
        has_condition_value = "condition_value" in rule
        context_expression = _optional_text(rule.get("context_expression"))
        tool_name = _optional_text(rule.get("tool_name"))
        stale_condition = _optional_text(rule.get("condition"))

        if stale_condition:
            raise WorkflowGraphCompileError(
                f"transition rule {source_name!r} -> {target_name!r} uses removed field "
                "'condition'. Use condition_type='context_equals' with condition_key/"
                "condition_value, condition_type='context_expression' with "
                "context_expression, or condition_type='tool_called' with tool_name."
            )

        if normalized_condition_type in {"expression", "llm", "string_llm"}:
            raise WorkflowGraphCompileError(
                f"transition rule {source_name!r} -> {target_name!r} uses "
                f"condition_type={condition_type!r}; AG2 Network workflow "
                "routing is deterministic. Move this intent classification to "
                "the control plane or set a context variable/tool result first."
            )

        source_id = _agent_id(source_name, agent_ids)
        target = _target_for(target_name, agent_ids, rule.get("transition_target"))

        if transition_type == "after_turn":
            if (
                normalized_condition_type
                or condition_key
                or context_expression
                or tool_name
                or (has_condition_value and condition_value is not None)
            ):
                raise WorkflowGraphCompileError(
                    f"transition rule {source_name!r} -> {target_name!r} uses after_turn "
                    "with condition fields"
                )
            when = FromSpeaker(source_id)
        elif transition_type == "condition":
            if normalized_condition_type not in _SUPPORTED_CONDITION_TYPES:
                raise WorkflowGraphCompileError(
                    f"transition rule {source_name!r} -> {target_name!r} uses unsupported "
                    f"condition_type={condition_type!r}"
                )
            if normalized_condition_type == "context_equals":
                if not condition_key:
                    raise WorkflowGraphCompileError(
                        f"transition rule {source_name!r} -> {target_name!r} requires condition_key"
                    )
                if tool_name:
                    raise WorkflowGraphCompileError(
                        f"transition rule {source_name!r} -> {target_name!r} must not mix "
                        "context_equals with tool_name"
                    )
                if context_expression:
                    raise WorkflowGraphCompileError(
                        f"transition rule {source_name!r} -> {target_name!r} must not mix "
                        "context_equals with context_expression"
                    )
                when = SourceScopedContextEquals(  # type: ignore[assignment]
                    source_agent_id=source_id,
                    key=condition_key,
                    value=condition_value,
                )
            elif normalized_condition_type == "context_expression":
                if not context_expression:
                    raise WorkflowGraphCompileError(
                        f"transition rule {source_name!r} -> {target_name!r} requires context_expression"
                    )
                if condition_key or (has_condition_value and condition_value is not None) or tool_name:
                    raise WorkflowGraphCompileError(
                        f"transition rule {source_name!r} -> {target_name!r} must not mix "
                        "context_expression with condition_key/condition_value/tool_name"
                    )
                try:
                    when = SourceScopedContextExpression(  # type: ignore[assignment]
                        source_agent_id=source_id,
                        expression=context_expression,
                    )
                except (SyntaxError, ValueError) as err:
                    raise WorkflowGraphCompileError(
                        f"transition rule {source_name!r} -> {target_name!r} has invalid "
                        f"context_expression: {err}"
                    ) from err
            else:
                if not tool_name:
                    raise WorkflowGraphCompileError(
                        f"transition rule {source_name!r} -> {target_name!r} requires tool_name"
                    )
                if condition_key or context_expression or (has_condition_value and condition_value is not None):
                    raise WorkflowGraphCompileError(
                        f"transition rule {source_name!r} -> {target_name!r} must not mix "
                        "tool_called with context condition fields"
                    )
                when = SourceScopedToolCalled(  # type: ignore[assignment]
                    source_agent_id=source_id,
                    tool_name=tool_name,
                )
        else:
            raise WorkflowGraphCompileError(
                f"transition rule {source_name!r} -> {target_name!r} uses unsupported "
                f"transition_type={transition_type!r}"
            )

        transitions.append(Transition(when=when, then=target, priority=index))

    initial_speaker = _agent_id(initial_agent_name, agent_ids)
    return TransitionGraph(
        initial_speaker=initial_speaker,
        transitions=transitions,
        default_target=TerminateTarget(reason="no_transition_matched"),
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
    """Resolve the next workflow speaker through AG2 beta `WorkflowAdapter`.

    Returns an agent name, `"user"` for a pause boundary, or `"terminate"`.
    Mozaiks owns transport, persistence, and UI integration; AG2's workflow
    adapter owns the state fold that advances from one agent turn to the next.
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
        event_type=EV_PACKET,
        event_data={
            "text": "",
            "context_updates": {
                "set": dict(context_variables or {}),
                "delete": [],
            },
        },
    )

    next_state = WorkflowAdapter().fold(envelope, state)
    return _route_name(next_state.expected_next_speaker, names_by_id)


def _target_for(target_name: str, agent_ids: Mapping[str, str], transition_target: Any) -> Any:
    normalized_target = target_name.strip().lower()
    declared_target = str(transition_target or "").strip().lower()
    if normalized_target in _SPECIAL_TERMINATE or declared_target == "terminatetarget":
        if normalized_target != "terminate":
            raise WorkflowGraphCompileError(
                "TerminateTarget transitions must use target_agent='terminate'"
            )
        return TerminateTarget(reason="workflow_complete")
    if normalized_target in _SPECIAL_USER:
        return AgentTarget("user")
    if target_name.strip() not in agent_ids:
        raise WorkflowGraphCompileError(
            f"transition target {target_name!r} is not a declared agent, 'user', or 'terminate'"
        )
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
        raise WorkflowGraphCompileError(f"transition rule is missing {key!r}: {dict(rule)!r}")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


__all__ = [
    "SourceScopedContextEquals",
    "SourceScopedContextExpression",
    "SourceScopedToolCalled",
    "WorkflowGraphCompileError",
    "compile_transition_rules_to_graph",
    "resolve_next_agent",
]
