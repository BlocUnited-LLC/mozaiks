"""Drift guard: every factory workflow must compile under context authority.

The context authority policy (#298/#344) validates at graph-compile time that
every routing context variable has an authorized deterministic writer. That
validation runs at runtime, so a contract change that breaks it produces a
green CI and a dead product: workflows fail instantly with zero agent
activity the moment a user starts a conversation.

This test replays the runtime's own construction (definitions ->
build_context_authority_policy -> validate_transition_context_authority) for
every shipped factory workflow, so any future policy/YAML drift fails a PR
instead of a user session.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mozaiksai.core.workflow.context.authority import (
    AGENT_TEXT_WRITER,
    DETERMINISTIC_TOOL_WRITER,
    SENTINEL_TEXT_TRIGGER_WRITER,
    build_context_authority_policy,
    validate_transition_context_authority,
)

WORKFLOWS_ROOT = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"


def _workflow_dirs() -> list[Path]:
    dirs = []
    for entry in sorted(WORKFLOWS_ROOT.iterdir()):
        if (entry / "context_variables.yaml").exists() and (entry / "transition_graph.yaml").exists():
            dirs.append(entry)
    assert dirs, "no factory workflows found — path drift in this test"
    return dirs


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@pytest.mark.parametrize("workflow_dir", _workflow_dirs(), ids=lambda p: p.name)
def test_workflow_routing_context_has_deterministic_writers(workflow_dir: Path):
    definitions = _load(workflow_dir / "context_variables.yaml").get("definitions") or {}
    transition_rules = _load(workflow_dir / "transition_graph.yaml").get("transition_rules") or []

    policy = build_context_authority_policy(
        workflow_name=workflow_dir.name,
        definitions=definitions,
        transition_rules=transition_rules,
    )
    # Raises ContextAuthorityError on any routing variable without an
    # authorized deterministic writer — exactly what graph compile does.
    validate_transition_context_authority(
        workflow_name=workflow_dir.name,
        policy=policy,
        transition_rules=transition_rules,
    )


@pytest.mark.parametrize("workflow_dir", _workflow_dirs(), ids=lambda p: p.name)
def test_workflow_declared_defaults_match_declared_types(workflow_dir: Path):
    """A default that violates the declared type poisons persistence: the
    replay guard fail-closes on it and the whole run crashes at persist time
    (e.g. `type: object` with `default: []`)."""
    from mozaiksai.core.workflow.context.authority import _is_valid_context_value

    definitions = _load(workflow_dir / "context_variables.yaml").get("definitions") or {}
    mismatches = []
    for key, definition in definitions.items():
        if not isinstance(definition, dict):
            continue
        source = definition.get("source") or {}
        if not isinstance(source, dict) or "default" not in source:
            continue
        default = source["default"]
        if default is not None and not _is_valid_context_value(default, value_type=definition.get("type")):
            mismatches.append(f"{key}: type={definition.get('type')} default={default!r}")
    assert not mismatches, f"{workflow_dir.name} declared defaults violate declared types: {mismatches}"


def test_sentinel_trigger_is_deterministic_writer():
    definitions = {
        "interview_complete": {
            "type": "boolean",
            "source": {
                "type": "state",
                "default": False,
                "triggers": [
                    {"type": "agent_text", "agent": "InterviewAgent", "match": {"equals": "NEXT"}}
                ],
            },
        }
    }
    rules = [
        {
            "source_agent": "InterviewAgent",
            "target_agent": "PlanAgent",
            "transition_type": "condition",
            "condition_type": "context_equals",
            "condition_key": "interview_complete",
            "condition_value": True,
        }
    ]
    policy = build_context_authority_policy(
        workflow_name="SentinelSmoke", definitions=definitions, transition_rules=rules
    )
    authority = policy.variables["interview_complete"]
    assert SENTINEL_TEXT_TRIGGER_WRITER in authority.writer_ids
    assert policy.can_write("interview_complete", writer_id=SENTINEL_TEXT_TRIGGER_WRITER)
    # Freeform model text is still banned from routing state.
    assert not policy.can_write("interview_complete", writer_id=AGENT_TEXT_WRITER)
    validate_transition_context_authority(
        workflow_name="SentinelSmoke", policy=policy, transition_rules=rules
    )


def test_freeform_capture_trigger_is_not_deterministic():
    definitions = {
        "captured_note_complete": {
            "type": "string",
            "source": {
                "type": "state",
                "triggers": [
                    {
                        "type": "agent_text",
                        "agent": "NoteAgent",
                        "match": {"regex": "NOTE: (.+)"},
                        "value": "$1",
                    }
                ],
            },
        }
    }
    policy = build_context_authority_policy(
        workflow_name="CaptureSmoke", definitions=definitions, transition_rules=[]
    )
    authority = policy.variables["captured_note_complete"]
    assert SENTINEL_TEXT_TRIGGER_WRITER not in authority.writer_ids


def _agent_text_trigger_effective_writer(trigger: dict) -> str:
    """Mirror runtime writer resolution for an agent_text trigger.

    A fixed-value sentinel (equals/contains match, no $1 capture) resolves to
    the deterministic SENTINEL_TEXT_TRIGGER_WRITER; anything else (regex, or a
    $1 capture) resolves to the freeform AGENT_TEXT_WRITER.
    """
    match = trigger.get("match") or {}
    is_fixed_match = bool(match.get("equals") or match.get("contains"))
    captures = str(trigger.get("value") or "") == "$1"
    if is_fixed_match and not captures:
        return SENTINEL_TEXT_TRIGGER_WRITER
    return AGENT_TEXT_WRITER


@pytest.mark.parametrize("workflow_dir", _workflow_dirs(), ids=lambda p: p.name)
def test_agent_text_triggers_can_write_their_target_key(workflow_dir: Path):
    """Every declared agent_text trigger must be able to write the key it
    targets under the workflow's own authority policy.

    The compile guard only proves a routing key has *some* authorized writer.
    It does not prove the *declared trigger mechanism* is one of them. A
    regex agent_text trigger on a `_complete`-suffixed routing key resolves to
    the freeform AGENT_TEXT writer, which closed routing state refuses — so
    the key can never be set and the workflow fails the instant the agent
    emits its completion signal (live AgentGenerator interview_complete
    failure). Compile stayed green; the run died.
    """
    definitions = _load(workflow_dir / "context_variables.yaml").get("definitions") or {}
    transition_rules = _load(workflow_dir / "transition_graph.yaml").get("transition_rules") or []
    policy = build_context_authority_policy(
        workflow_name=workflow_dir.name,
        definitions=definitions,
        transition_rules=transition_rules,
    )
    unsatisfiable = []
    for key, definition in definitions.items():
        if not isinstance(definition, dict):
            continue
        source = definition.get("source") or {}
        for trigger in (source.get("triggers") or []) if isinstance(source, dict) else []:
            if not isinstance(trigger, dict) or trigger.get("type") != "agent_text":
                continue
            writer = _agent_text_trigger_effective_writer(trigger)
            if not policy.can_write(key, writer_id=writer):
                unsatisfiable.append(
                    f"{key}: agent_text trigger match={trigger.get('match')} "
                    f"resolves to writer={writer!r} which cannot write this key"
                )
    assert not unsatisfiable, (
        f"{workflow_dir.name} has agent_text triggers that can never write their "
        f"target key: {unsatisfiable}"
    )


def test_routing_state_accepts_deterministic_tool_writer():
    definitions = {
        "concept_presented": {
            "type": "boolean",
            "source": {"type": "state", "default": False},
        }
    }
    rules = [
        {
            "source_agent": "GapAnalysisAgent",
            "target_agent": "user",
            "transition_type": "condition",
            "condition_type": "context_equals",
            "condition_key": "concept_presented",
            "condition_value": True,
        }
    ]
    policy = build_context_authority_policy(
        workflow_name="ToolRoutingSmoke", definitions=definitions, transition_rules=rules
    )
    assert policy.can_write("concept_presented", writer_id=DETERMINISTIC_TOOL_WRITER)
    # Freeform writers stay banned for closed routing state.
    assert not policy.can_write("concept_presented", writer_id=AGENT_TEXT_WRITER)
    assert not policy.can_write("concept_presented", writer_id="tool_writeback")
    validate_transition_context_authority(
        workflow_name="ToolRoutingSmoke", policy=policy, transition_rules=rules
    )
