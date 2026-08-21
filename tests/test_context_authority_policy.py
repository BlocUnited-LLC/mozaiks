from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mozaiksai.core.adapters.ag2_network_runner import _authorized_context_updates
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import (
    AGENT_TEXT_WRITER,
    CALLER_INPUT_WRITER,
    CONTEXT_BRIDGE_WRITER,
    DETERMINISTIC_TOOL_WRITER,
    PERSISTED_REPLAY_WRITER,
    UI_RESPONSE_TRIGGER_WRITER,
    USER_TEXT_TRIGGER_WRITER,
    ContextAuthorityError,
    ScopedContextWriter,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.context.derived import DerivedContextManager
from mozaiksai.core.workflow.context.schema import load_context_variables_config
from mozaiksai.core.workflow.execution.network_graph import (
    WorkflowGraphCompileError,
    compile_transition_rules_to_graph,
    resolve_next_agent,
)


def _policy():
    plan = load_context_variables_config(
        {
            "definitions": {
                "app_id": {
                    "type": "string",
                    "source": {"type": "state", "default": "app_1"},
                },
                "build_mode": {
                    "type": "string",
                    "source": {"type": "state", "default": "greenfield"},
                },
                "revision_scope": {
                    "type": "string",
                    "source": {"type": "state", "default": "none"},
                },
                "task_run_mode": {
                    "type": "string",
                    "source": {"type": "state", "default": "single"},
                },
                "sequence_status": {
                    "type": "string",
                    "source": {"type": "state", "default": "pending"},
                },
                "app_validation_status": {
                    "type": "string",
                    "authority_class": "closed_writer_quality_state",
                    "routing": True,
                    "writer_ids": ["deterministic_tool"],
                    "source": {"type": "state", "default": "pending"},
                },
                "ordinary_state": {
                    "type": "string",
                    "source": {"type": "state", "default": "draft"},
                },
                "derived_note": {
                    "type": "boolean",
                    "authority_class": "derived_information",
                    "writer_ids": ["agent_text"],
                    "source": {
                        "type": "state",
                        "default": False,
                        "triggers": [
                            {
                                "type": "agent_text",
                                "agent": "Narrator",
                                "match": {"contains": "NOTE"},
                            }
                        ],
                    },
                },
                "user_ready": {
                    "type": "boolean",
                    "source": {
                        "type": "state",
                        "default": False,
                        "triggers": [{"type": "user_text", "match": {"contains": "ready"}}],
                    },
                },
                "review_complete": {
                    "type": "boolean",
                    "authority_class": "closed_writer_routing_state",
                    "routing": True,
                    "writer_ids": ["ui_response_trigger"],
                    "source": {
                        "type": "state",
                        "default": False,
                        "triggers": [
                            {
                                "type": "ui_response",
                                "tool": "approve_review",
                                "response_key": "approved",
                            }
                        ],
                    },
                },
            },
            "agents": {},
        }
    )
    transitions = [
        {
            "source_agent": "QualityAgent",
            "target_agent": "ReviewAgent",
            "transition_type": "condition",
            "condition_type": "context_equals",
            "condition_key": "app_validation_status",
            "condition_value": "passed",
        }
    ]
    return build_context_authority_policy(
        workflow_name="AuthorityFlow",
        definitions=plan.definitions,
        transition_rules=transitions,
    )


@pytest.mark.parametrize(
    "key",
    ["app_id", "user_id", "build_mode", "revision_scope", "task_run_mode", "sequence_status"],
)
def test_caller_cannot_set_authority_or_routing_keys(key: str) -> None:
    policy = _policy()

    with pytest.raises(ContextAuthorityError):
        policy.require_can_write(key, writer_id=CALLER_INPUT_WRITER)


def test_context_bridge_rejects_authority_key_write() -> None:
    policy = _policy()
    bridge = ContextVariablesBridge({}, authority_policy=policy)

    with pytest.raises(ContextAuthorityError):
        bridge.set("app_id", "attacker")

    assert "app_id" not in bridge.snapshot()


def test_agent_text_cannot_set_routing_or_quality_status() -> None:
    policy = _policy()

    with pytest.raises(ContextAuthorityError):
        policy.require_can_write("app_validation_status", writer_id=AGENT_TEXT_WRITER)

    policy.require_can_write("derived_note", writer_id=AGENT_TEXT_WRITER)


def test_ui_and_user_triggers_reject_undeclared_keys() -> None:
    policy = _policy()

    with pytest.raises(ContextAuthorityError):
        policy.require_can_write("undeclared_ready", writer_id=UI_RESPONSE_TRIGGER_WRITER)
    with pytest.raises(ContextAuthorityError):
        policy.require_can_write("undeclared_ready", writer_id=USER_TEXT_TRIGGER_WRITER)


def test_persisted_replay_skips_declared_non_persisted_authority_before_authorization() -> None:
    policy = _policy()
    diagnostics: list[str] = []

    result = policy.filter_for_replay(
        {"app_id": {"spoofed": True}},
        writer_id=PERSISTED_REPLAY_WRITER,
        diagnostics=diagnostics,
    )

    assert result == {}
    assert diagnostics == ["context_authority.replay_skipped_non_persisted workflow=AuthorityFlow key=app_id"]


def test_persistence_skips_declared_non_persisted_authority_before_value_validation() -> None:
    policy = _policy()
    diagnostics: list[str] = []

    result = policy.filter_for_persistence(
        {"app_id": {"spoofed": True}, "ordinary_state": "saved"},
        diagnostics=diagnostics,
    )

    assert result == {"ordinary_state": "saved"}
    assert diagnostics == [
        "context_authority.persistence_skipped_non_persisted workflow=AuthorityFlow key=app_id"
    ]


def test_persisted_replay_allows_known_replayable_quality_state() -> None:
    policy = _policy()

    result = policy.filter_for_replay(
        {"app_validation_status": "passed"},
        writer_id=PERSISTED_REPLAY_WRITER,
    )

    assert result == {"app_validation_status": "passed"}


def test_persisted_replay_rejects_persisted_immutable_runtime_authority() -> None:
    plan = load_context_variables_config(
        {
            "definitions": {
                "app_id": {
                    "type": "string",
                    "persisted": True,
                    "source": {"type": "state", "default": "app_1"},
                },
            },
            "agents": {},
        }
    )

    with pytest.raises(ContextAuthorityError, match="not replayable"):
        build_context_authority_policy(
            workflow_name="PersistedImmutableFlow",
            definitions=plan.definitions,
            transition_rules=[],
        )


def test_persisted_replay_drops_stale_unknown_historical_keys_with_diagnostics() -> None:
    policy = _policy()
    diagnostics: list[str] = []

    result = policy.filter_for_replay(
        {"unknown_historical_key": "old", "ordinary_state": "restored"},
        writer_id=PERSISTED_REPLAY_WRITER,
        diagnostics=diagnostics,
    )

    assert result == {"ordinary_state": "restored"}
    assert diagnostics == [
        "context_authority.replay_dropped_unknown workflow=AuthorityFlow key=unknown_historical_key"
    ]


def test_packet_and_raw_context_set_guards_reject_bypass_attempts() -> None:
    policy = _policy()

    with pytest.raises(ContextAuthorityError):
        _authorized_context_updates(
            {"app_id": "evil"},
            writer_id=CONTEXT_BRIDGE_WRITER,
            context_authority_policy=policy,
        )


def test_authorized_deterministic_quality_tool_can_update_declared_quality_key() -> None:
    policy = _policy()
    container = create_context_container(
        initial={},
        authority_policy=policy,
        writer_id=DETERMINISTIC_TOOL_WRITER,
    )

    writer = ScopedContextWriter(policy=policy, writer_id=DETERMINISTIC_TOOL_WRITER)
    writer.set(container, "app_validation_status", "passed")

    assert container.get("app_validation_status") == "passed"


def test_ordinary_declared_mutable_workflow_state_still_works() -> None:
    policy = _policy()
    bridge = ContextVariablesBridge({}, authority_policy=policy)

    bridge.set("ordinary_state", "updated")

    assert bridge.consume_context_updates()["set"] == {"ordinary_state": "updated"}


def test_derived_agent_text_only_updates_declared_non_authority_key() -> None:
    policy = _policy()
    plan = load_context_variables_config(
        {
            "definitions": {
                "derived_note": {
                    "type": "boolean",
                    "authority_class": "derived_information",
                    "writer_ids": ["agent_text"],
                    "source": {
                        "type": "state",
                        "default": False,
                        "triggers": [
                            {
                                "type": "agent_text",
                                "agent": "Narrator",
                                "match": {"contains": "NOTE"},
                            }
                        ],
                    },
                }
            },
            "agents": {},
        }
    )
    context = create_context_container(initial={}, authority_policy=policy)
    context._mozaiks_context_definitions = plan.definitions
    manager = DerivedContextManager("AuthorityFlow", {}, context)

    assert manager.apply_agent_text("Narrator", "NOTE complete") == {"derived_note": True}
    assert context.get("derived_note") is True


def test_transition_graph_rejects_non_routing_conditions() -> None:
    policy = _policy()

    with pytest.raises(WorkflowGraphCompileError, match="non-routing"):
        compile_transition_rules_to_graph(
            [
                {
                    "source_agent": "Planner",
                    "target_agent": "Reviewer",
                    "transition_type": "condition",
                    "condition_type": "context_equals",
                    "condition_key": "ordinary_state",
                    "condition_value": "updated",
                }
            ],
            initial_agent_name="Planner",
            agent_id_by_name={"Planner": "Planner", "Reviewer": "Reviewer"},
            context_authority_policy=policy,
        )


def test_transition_graph_rejects_unknown_context_condition_key() -> None:
    policy = _policy()

    with pytest.raises(WorkflowGraphCompileError, match="unknown"):
        compile_transition_rules_to_graph(
            [
                {
                    "source_agent": "Planner",
                    "target_agent": "Reviewer",
                    "transition_type": "condition",
                    "condition_type": "context_equals",
                    "condition_key": "undeclared_flag",
                    "condition_value": True,
                }
            ],
            initial_agent_name="Planner",
            agent_id_by_name={"Planner": "Planner", "Reviewer": "Reviewer"},
            context_authority_policy=policy,
        )


def test_transition_graph_rejects_routing_key_without_deterministic_writer() -> None:
    plan = load_context_variables_config(
        {
            "definitions": {
                "model_chosen_route": {
                    "type": "string",
                    "authority_class": "closed_writer_routing_state",
                    "routing": True,
                    "writer_ids": ["agent_text"],
                    "source": {"type": "state", "default": "draft"},
                }
            },
            "agents": {},
        }
    )
    policy = build_context_authority_policy(
        workflow_name="BadRouteFlow",
        definitions=plan.definitions,
        transition_rules=[
            {
                "source_agent": "Planner",
                "target_agent": "Reviewer",
                "transition_type": "condition",
                "condition_type": "context_equals",
                "condition_key": "model_chosen_route",
                "condition_value": "review",
            }
        ],
    )

    with pytest.raises(WorkflowGraphCompileError, match="deterministic writer"):
        compile_transition_rules_to_graph(
            [
                {
                    "source_agent": "Planner",
                    "target_agent": "Reviewer",
                    "transition_type": "condition",
                    "condition_type": "context_equals",
                    "condition_key": "model_chosen_route",
                    "condition_value": "review",
                }
            ],
            initial_agent_name="Planner",
            agent_id_by_name={"Planner": "Planner", "Reviewer": "Reviewer"},
            context_authority_policy=policy,
        )


def test_transition_graph_accepts_declared_deterministic_routing_condition() -> None:
    policy = _policy()

    graph = compile_transition_rules_to_graph(
        [
            {
                "source_agent": "QualityAgent",
                "target_agent": "ReviewAgent",
                "transition_type": "condition",
                "condition_type": "context_equals",
                "condition_key": "app_validation_status",
                "condition_value": "passed",
            }
        ],
        initial_agent_name="QualityAgent",
        agent_id_by_name={"QualityAgent": "QualityAgent", "ReviewAgent": "ReviewAgent"},
        context_authority_policy=policy,
    )

    assert graph.to_dict()["initial_speaker"] == "QualityAgent"
    assert (
        resolve_next_agent(
            graph,
            current_agent_name="QualityAgent",
            context_variables={"app_validation_status": "passed"},
            agent_name_by_id={"QualityAgent": "QualityAgent", "ReviewAgent": "ReviewAgent"},
            participant_order=["QualityAgent", "ReviewAgent"],
        )
        == "ReviewAgent"
    )


def test_factory_context_inventory_classifies_authority_like_keys() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
    authority_like_parts = (
        "app_id",
        "user_id",
        "build_mode",
        "revision_scope",
        "task_run_mode",
        "sequence_status",
        "quality",
        "validation_status",
        "acceptance_status",
        "tests_passed",
        "permission",
        "entitlement",
        "secret",
        "token",
    )
    checked = 0

    for path in sorted(workflows_root.glob("*/context_variables.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        plan = load_context_variables_config(raw)
        transition_path = path.parent / "transition_graph.yaml"
        transition_rules = []
        if transition_path.exists():
            transition_raw = yaml.safe_load(transition_path.read_text(encoding="utf-8")) or {}
            transition_rules = transition_raw.get("transition_rules") or []
        policy = build_context_authority_policy(
            workflow_name=path.parent.name,
            definitions=plan.definitions,
            transition_rules=transition_rules,
        )
        assert set(policy.variables) == set(plan.definitions)
        for key, authority in policy.variables.items():
            if any(part in key.lower() for part in authority_like_parts):
                assert authority.authority_class.value != "mutable_workflow_state", key
                checked += 1

    assert checked > 0


def test_persisted_replay_rejects_known_malformed_value() -> None:
    policy = _policy()

    with pytest.raises(ContextAuthorityError, match="invalid_value"):
        policy.filter_for_replay({"review_complete": "true"}, writer_id=PERSISTED_REPLAY_WRITER)


def test_unresolved_workflow_declarations_fail_replay_closed() -> None:
    """An unresolved policy must not masquerade as 'every stored key is stale'."""

    policy = build_context_authority_policy(
        workflow_name="UnloadedFlow",
        definitions={},
        transition_rules=[],
        declarations_resolved=False,
    )

    with pytest.raises(ContextAuthorityError, match="unresolved_declarations"):
        policy.filter_for_replay(
            {"interview_complete": True},
            writer_id=PERSISTED_REPLAY_WRITER,
        )


def test_unresolved_workflow_declarations_fail_persistence_closed() -> None:
    policy = build_context_authority_policy(
        workflow_name="UnloadedFlow",
        definitions={},
        transition_rules=[],
        declarations_resolved=False,
    )

    with pytest.raises(ContextAuthorityError, match="unresolved_declarations"):
        policy.filter_for_persistence({"interview_complete": True})


def test_unresolved_declarations_tolerate_empty_value_sets() -> None:
    """Nothing to write is not a failure, even when declarations are unresolved."""

    policy = build_context_authority_policy(
        workflow_name="UnloadedFlow",
        definitions={},
        transition_rules=[],
        declarations_resolved=False,
    )

    assert policy.filter_for_replay({}, writer_id=PERSISTED_REPLAY_WRITER) == {}
    assert policy.filter_for_persistence({}) == {}


def test_resolved_workflow_with_no_declarations_still_drops_stale_keys() -> None:
    """A workflow that legitimately declares nothing keeps stale-key drop semantics."""

    policy = build_context_authority_policy(
        workflow_name="NoContextFlow",
        definitions={},
        transition_rules=[],
    )
    diagnostics: list[str] = []

    result = policy.filter_for_replay(
        {"stale_key": "old"},
        writer_id=PERSISTED_REPLAY_WRITER,
        diagnostics=diagnostics,
    )

    assert result == {}
    assert diagnostics == [
        "context_authority.replay_dropped_unknown workflow=NoContextFlow key=stale_key"
    ]
