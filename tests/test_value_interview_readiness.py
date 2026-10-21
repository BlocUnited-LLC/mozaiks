"""Concept intake readiness is typed, bounded, and owned by the deterministic tool."""

from types import MappingProxyType

import pytest

from factory_app.workflows.ValueEngine.tools.record_value_interview import record_value_interview
from mozaiksai.core.workflow.context.authority import (
    AGENT_TEXT_WRITER,
    SENTINEL_TEXT_TRIGGER_WRITER,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec
from mozaiksai.core.workflow.execution.network_graph import (
    compile_transition_rules_to_graph,
    resolve_next_agent,
)
from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome
from tests.test_factory_auto_tool_acceptance import (
    _PatternContext,
    factory_manager,  # noqa: F401
)


@pytest.fixture(autouse=True)
def value_config(request):
    manager = request.getfixturevalue("factory_manager")
    info = manager.reload_workflow("ValueEngine")
    assert not info.get("error"), info
    return manager.get_config("ValueEngine")


@pytest.mark.parametrize("outcome", ["needs_input", "ready"])
def test_interview_readiness_comes_from_typed_output_not_message(outcome):
    message = "NEXT is ordinary quoted text; it cannot choose the next agent."
    assert record_value_interview(message, {
        "structured_output": MappingProxyType({"agent_message": message, "outcome": outcome}),
    }) == {"outcome": outcome}


@pytest.mark.parametrize("payload", [
    None,
    {"agent_message": "NEXT"},
    {"outcome": "ready"},
    {"agent_message": "Confirmed", "outcome": "blocked"},
    {"agent_message": "Confirmed", "outcome": "maybe"},
    {"agent_message": "Confirmed", "outcome": True},
    {"agent_message": 7, "outcome": "ready"},
    {"agent_message": None, "outcome": "ready"},
    {"agent_message": "Confirmed", "outcome": "ready", "target_agent": "ResearchAgent"},
    {"agent_message": "", "outcome": "ready"},
    {"agent_message": " \n\t", "outcome": "ready"},
])
def test_invalid_interview_result_cannot_advance(payload):
    with pytest.raises(ValueError):
        record_value_interview((payload or {}).get("agent_message", ""), {"structured_output": payload})


def test_interview_requires_runtime_context_and_exact_validated_message():
    with pytest.raises(ValueError, match="runtime context"):
        record_value_interview("Confirmed")
    with pytest.raises(ValueError, match="must match"):
        record_value_interview("Different question", {
            "structured_output": {"agent_message": "Approved question", "outcome": "ready"},
        })


def test_readiness_and_attempts_have_closed_deterministic_writers(value_config):
    definitions = value_config["context_variables"]["definitions"]
    policy = build_context_authority_policy(workflow_name="ValueEngine", definitions=definitions)
    for key, default in (("interview_outcome", "blocked"), ("interview_attempts", 0)):
        definition = definitions[key]
        assert definition["source"]["type"] == "state"
        assert definition["source"]["default"] == default
        assert not definition["source"].get("triggers")
        assert definition["writer_ids"] == ["deterministic_tool"]
        assert definition["persisted"] is True
        policy.require_can_write(key, writer_id="deterministic_tool")
        assert not policy.can_write(key, writer_id=AGENT_TEXT_WRITER)
        assert not policy.can_write(key, writer_id=SENTINEL_TEXT_TRIGGER_WRITER)
        assert not policy.can_write(key, writer_id="caller_input")
    assert "interview_complete" not in definitions
    assert "interview_complete" not in str(value_config["transition_graph"])


@pytest.fixture
def interview_tool(value_config):
    entry = next(tool for tool in value_config["tools"] if tool["agent"] == "ValueInterviewAgent")
    assert entry["auto_tool_call"] is True
    assert entry["bind_to_agent"] is False
    spec = ToolOutcomeSpec.model_validate(entry["outcome"])
    assert spec.max_attempts == 10
    return wrap_tool_outcome(record_value_interview, spec)


def _invoke(tool, context, outcome, message="One remaining product question"):
    return tool(message, StructuredOutputOverlay(context, {
        "agent_message": message, "outcome": outcome,
    }))


def test_interview_attempt_cap_cannot_be_bypassed_by_ready_output(interview_tool):
    context = _PatternContext()
    context.data.update(interview_outcome="blocked", interview_attempts=0)
    for attempt in range(1, 11):
        assert _invoke(interview_tool, context, "needs_input") == {"outcome": "needs_input"}
        assert context.get("interview_attempts") == attempt
    assert _invoke(interview_tool, context, "ready") == {
        "outcome": "blocked", "outcome_error": "attempts_exhausted",
    }
    assert context.get("interview_outcome") == "blocked"
    assert context.get("interview_attempts") == 10


@pytest.mark.parametrize("previous", ["ready", "blocked"])
def test_completed_or_blocked_interview_cannot_retry(interview_tool, previous):
    context = _PatternContext()
    context.data.update(interview_outcome=previous, interview_attempts=1)
    assert _invoke(interview_tool, context, "ready") == {
        "outcome": "blocked", "outcome_error": "retry_not_permitted",
    }
    assert context.get("interview_outcome") == "blocked"
    assert context.get("interview_attempts") == 1


def test_invalid_tool_output_records_blocked_instead_of_preserving_readiness(interview_tool):
    context = _PatternContext()
    context.data.update(interview_outcome="needs_input", interview_attempts=1)
    result = interview_tool("Different question", StructuredOutputOverlay(context, {
        "agent_message": "Approved question", "outcome": "ready",
    }))
    assert result == {"outcome": "blocked", "outcome_error": "tool_execution_failed"}
    assert context.get("interview_outcome") == "blocked"
    assert context.get("interview_attempts") == 2


@pytest.fixture
def route(value_config):
    names = list(value_config["agents"]["agents"])
    rules = value_config["transition_graph"]["transition_rules"]
    policy = build_context_authority_policy(
        workflow_name="ValueEngine", definitions=value_config["context_variables"]["definitions"],
        transition_rules=rules,
    )
    graph = compile_transition_rules_to_graph(
        rules, initial_agent_name="user", agent_id_by_name={name: name for name in names},
        context_authority_policy=policy,
    )

    def resolve(source, context):
        return resolve_next_agent(
            graph, current_agent_name=source, context_variables=context,
            agent_name_by_id={name: name for name in names}, participant_order=[*names, "user"],
        )

    return resolve


@pytest.mark.parametrize(("outcome", "expected"), [
    ("needs_input", "user"), ("ready", "ResearchAgent"),
    ("blocked", "terminate"), ("NEXT", "terminate"), (None, "terminate"),
])
def test_interview_graph_only_advances_validated_ready(route, value_config, outcome, expected):
    context = {} if outcome is None else {"interview_outcome": outcome}
    assert route("ValueInterviewAgent", context) == expected
    terminal_rules = [rule for rule in value_config["transition_graph"]["transition_rules"]
                      if rule["source_agent"] == "ValueInterviewAgent" and rule["target_agent"] == "terminate"]
    assert terminal_rules
    assert all(rule["termination_reason"] == "workflow_failed" for rule in terminal_rules)


@pytest.mark.parametrize(("context", "expected"), [
    ({}, "terminate"),
    ({"interview_outcome": "blocked"}, "terminate"),
    ({"interview_outcome": "needs_input"}, "ValueInterviewAgent"),
    ({"interview_outcome": "ready"}, "terminate"),
    ({"interview_outcome": "ready", "concept_presented": True}, "GapAnalysisAgent"),
])
def test_user_reply_returns_only_to_the_waiting_stage(route, context, expected):
    assert route("user", context) == expected


def test_readiness_does_not_approve_the_concept(interview_tool):
    context = _PatternContext()
    context.data.update(interview_outcome="blocked", interview_attempts=0,
                        concept_presented=False, concept_review_outcome="blocked")
    assert _invoke(interview_tool, context, "ready", "I will prepare your concept.") == {"outcome": "ready"}
    assert context.get("concept_review_outcome") == "blocked"
    assert context.get("concept_presented") is False
