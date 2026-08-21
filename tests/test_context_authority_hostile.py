"""Hostile mutation tests for ContextAuthorityPolicy — Phase 3 review gate.

Each test attempts to mutate context through a distinct mutation vector.
Tests assert that the unauthorized mutation raises ContextAuthorityError
(fail-closed), not just logs.
"""
from __future__ import annotations

import pytest

from mozaiksai.core.adapters.ag2_network_runner import _authorized_context_updates
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import (
    AGENT_TEXT_WRITER,
    CALLER_INPUT_WRITER,
    CONTEXT_BRIDGE_WRITER,
    DETERMINISTIC_TOOL_WRITER,
    LIVE_USER_CONTEXT_WRITER,
    PERSISTED_REPLAY_WRITER,
    TASK_BATCH_WRITER,
    UI_RESPONSE_TRIGGER_WRITER,
    USER_TEXT_TRIGGER_WRITER,
    ContextAuthorityError,
    ScopedContextWriter,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.context.schema import load_context_variables_config
from mozaiksai.core.workflow.execution.network_graph import (
    WorkflowGraphCompileError,
    compile_transition_rules_to_graph,
)

# ---------------------------------------------------------------------------
# Shared policy fixture
# ---------------------------------------------------------------------------

def _hostile_policy():
    """Policy with immutable, routing, and quality keys alongside ordinary state."""
    plan = load_context_variables_config(
        {
            "definitions": {
                "app_id": {
                    "type": "string",
                    "source": {"type": "state", "default": "app_1"},
                },
                "user_id": {
                    "type": "string",
                    "source": {"type": "state", "default": "user_1"},
                },
                "routing_target": {
                    "type": "string",
                    "authority_class": "closed_writer_routing_state",
                    "routing": True,
                    "writer_ids": ["deterministic_tool"],
                    "source": {"type": "state", "default": "step_a"},
                },
                "app_validation_status": {
                    "type": "string",
                    "authority_class": "closed_writer_quality_state",
                    "writer_ids": ["deterministic_tool"],
                    "source": {"type": "state", "default": "pending"},
                },
                "ordinary_state": {
                    "type": "string",
                    "source": {"type": "state", "default": "draft"},
                },
                "config_data": {
                    "type": "object",
                    "source": {"type": "state", "default": {}},
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
            "source_agent": "RouterAgent",
            "target_agent": "WorkerAgent",
            "transition_type": "condition",
            "condition_type": "context_equals",
            "condition_key": "routing_target",
            "condition_value": "step_b",
        }
    ]
    return build_context_authority_policy(
        workflow_name="HostileTestFlow",
        definitions=plan.definitions,
        transition_rules=transitions,
    )


# ---------------------------------------------------------------------------
# Vector 1: Direct set via bridge.__setitem__ from unauthorized writer
# ---------------------------------------------------------------------------

def test_vector1_direct_set_routing_key_rejected() -> None:
    """bridge['routing.target'] = 'evil' from unauthorized writer must raise."""
    policy = _hostile_policy()
    bridge = ContextVariablesBridge({}, authority_policy=policy)

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        bridge["routing_target"] = "evil"

    assert bridge.get("routing_target") is None


def test_vector1_direct_set_immutable_key_rejected() -> None:
    """bridge['app_id'] = 'evil' from default context_bridge writer must raise."""
    policy = _hostile_policy()
    bridge = ContextVariablesBridge({}, authority_policy=policy)

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        bridge["app_id"] = "attacker"

    assert bridge.get("app_id") is None


def test_vector1_direct_set_quality_key_rejected() -> None:
    """bridge['app_validation_status'] = 'passed' from unauthorized writer must raise."""
    policy = _hostile_policy()
    bridge = ContextVariablesBridge({}, authority_policy=policy)

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        bridge["app_validation_status"] = "passed"

    assert bridge.get("app_validation_status") is None


# ---------------------------------------------------------------------------
# Vector 2: update() via _authorized_context_updates from unauthorized writer
# ---------------------------------------------------------------------------

def test_vector2_authorized_context_updates_rejects_immutable_key() -> None:
    """_authorized_context_updates with immutable key from context_bridge must raise."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        _authorized_context_updates(
            {"app_id": "evil"},
            writer_id=CONTEXT_BRIDGE_WRITER,
            context_authority_policy=policy,
        )


def test_vector2_authorized_context_updates_rejects_routing_key() -> None:
    """_authorized_context_updates with routing key from agent_text must raise."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        _authorized_context_updates(
            {"routing_target": "evil"},
            writer_id=AGENT_TEXT_WRITER,
            context_authority_policy=policy,
        )


def test_vector2_authorized_context_updates_rejects_unknown_key() -> None:
    """_authorized_context_updates with unknown key must raise (fail-closed)."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority.unknown_key"):
        _authorized_context_updates(
            {"undeclared_routing_key": "evil"},
            writer_id=CONTEXT_BRIDGE_WRITER,
            context_authority_policy=policy,
        )


# ---------------------------------------------------------------------------
# Vector 3: Nested dict mutation (known open vector — documented)
# ---------------------------------------------------------------------------

def test_vector3_nested_dict_mutation_bypasses_setitem() -> None:
    """Nested dict mutation via __getitem__ result is now blocked by freeze().

    Previously a KNOWN OPEN VECTOR: Python dict mutation on a returned reference
    bypassed __setitem__ policy. Fixed in cc/bridge-immutable-context:
    __getitem__ now returns freeze(value) — a recursively immutable MappingProxyType.
    Nested in-place mutation now raises TypeError.
    """
    policy = _hostile_policy()
    bridge = ContextVariablesBridge({"config_data": {"setting": "normal"}}, authority_policy=policy)

    # Attempt to mutate through __getitem__ — now raises TypeError (MappingProxyType)
    nested = bridge["config_data"]
    with pytest.raises(TypeError):
        nested["admin_override"] = True  # type: ignore[index]

    # Canonical state must be unchanged
    assert "admin_override" not in bridge["config_data"]
    assert bridge["config_data"]["setting"] == "normal"


def test_vector3_data_property_allows_direct_dict_write() -> None:
    """bridge.data is now removed — it raises AttributeError.

    Previously a KNOWN OPEN VECTOR: Direct write to the backing dict through
    .data property bypassed __setitem__ policy. Fixed in cc/bridge-immutable-context:
    .data now raises AttributeError, directing callers to bridge.snapshot().
    """
    policy = _hostile_policy()
    bridge = ContextVariablesBridge({"app_id": "legit"}, authority_policy=policy)

    # .data must raise — it has been removed
    with pytest.raises(AttributeError, match="snapshot"):
        _ = bridge.data

    # Canonical state must be unchanged
    assert bridge.get("app_id") == "legit"


# ---------------------------------------------------------------------------
# Vector 4: Tool return/write-back via ScopedContextWriter
# ---------------------------------------------------------------------------

def test_vector4_tool_writeback_deterministic_tool_can_write_quality_key() -> None:
    """A deterministic tool using ScopedContextWriter can update declared quality key."""
    policy = _hostile_policy()
    container = create_context_container(
        initial={},
        authority_policy=policy,
        writer_id=DETERMINISTIC_TOOL_WRITER,
    )
    writer = ScopedContextWriter(policy=policy, writer_id=DETERMINISTIC_TOOL_WRITER)
    writer.set(container, "app_validation_status", "passed")
    assert container.get("app_validation_status") == "passed"


def test_vector4_tool_writeback_ordinary_writer_cannot_write_quality_key() -> None:
    """An ordinary tool (CONTEXT_BRIDGE_WRITER) cannot write quality-gate keys."""
    policy = _hostile_policy()
    writer = ScopedContextWriter(policy=policy, writer_id=CONTEXT_BRIDGE_WRITER)
    container = create_context_container(initial={}, authority_policy=policy)

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        writer.set(container, "app_validation_status", "evil")


# ---------------------------------------------------------------------------
# Vector 5: Agent text writing routing/quality keys
# ---------------------------------------------------------------------------

def test_vector5_agent_text_cannot_write_routing_key() -> None:
    """AGENT_TEXT_WRITER cannot write a routing key."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        policy.require_can_write("routing_target", writer_id=AGENT_TEXT_WRITER)


def test_vector5_agent_text_cannot_write_quality_key() -> None:
    """AGENT_TEXT_WRITER cannot write a quality-gate key."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        policy.require_can_write("app_validation_status", writer_id=AGENT_TEXT_WRITER)


def test_vector5_agent_text_cannot_write_immutable_key() -> None:
    """AGENT_TEXT_WRITER cannot write an immutable identity key."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        policy.require_can_write("app_id", writer_id=AGENT_TEXT_WRITER)


# ---------------------------------------------------------------------------
# Vector 6: User/UI trigger writing undeclared or authority keys
# ---------------------------------------------------------------------------

def test_vector6_live_user_context_cannot_write_routing_key() -> None:
    """LIVE_USER_CONTEXT_WRITER cannot write routing keys."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        _authorized_context_updates(
            {"routing_target": "evil"},
            writer_id=LIVE_USER_CONTEXT_WRITER,
            context_authority_policy=policy,
        )


def test_vector6_ui_response_trigger_cannot_write_undeclared_key() -> None:
    """UI_RESPONSE_TRIGGER_WRITER cannot write undeclared keys."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        policy.require_can_write("undeclared_key", writer_id=UI_RESPONSE_TRIGGER_WRITER)


def test_vector6_user_text_trigger_cannot_write_undeclared_key() -> None:
    """USER_TEXT_TRIGGER_WRITER cannot write undeclared keys."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        policy.require_can_write("undeclared_key", writer_id=USER_TEXT_TRIGGER_WRITER)


def test_vector6_caller_input_cannot_write_immutable_keys() -> None:
    """CALLER_INPUT_WRITER cannot write identity/immutable keys."""
    policy = _hostile_policy()

    for key in ["app_id", "user_id"]:
        with pytest.raises(ContextAuthorityError, match="context_authority"):
            policy.require_can_write(key, writer_id=CALLER_INPUT_WRITER)


# ---------------------------------------------------------------------------
# Vector 7: Live resume context updates (user message context_updates)
# ---------------------------------------------------------------------------

def test_vector7_live_resume_user_context_rejects_routing_key() -> None:
    """Live resume context_updates via LIVE_USER_CONTEXT_WRITER cannot write routing keys."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        _authorized_context_updates(
            {"routing_target": "evil"},
            writer_id=LIVE_USER_CONTEXT_WRITER,
            context_authority_policy=policy,
        )


def test_vector7_live_resume_ordinary_key_is_allowed() -> None:
    """Live resume context_updates can write ordinary declared state keys."""
    policy = _hostile_policy()

    result = _authorized_context_updates(
        {"ordinary_state": "updated"},
        writer_id=LIVE_USER_CONTEXT_WRITER,
        context_authority_policy=policy,
    )
    assert result == {"ordinary_state": "updated"}


# ---------------------------------------------------------------------------
# Vector 8: EV_CONTEXT_SET / persisted replay
# ---------------------------------------------------------------------------

def test_vector8_persisted_replay_skips_non_persisted_immutable_key() -> None:
    """filter_for_replay skips declared non-persisted immutable keys before authorization."""
    policy = _hostile_policy()
    diagnostics: list[str] = []

    result = policy.filter_for_replay(
        {"app_id": "evil"},
        writer_id=PERSISTED_REPLAY_WRITER,
        diagnostics=diagnostics,
    )

    assert result == {}
    assert diagnostics == ["context_authority.replay_skipped_non_persisted workflow=HostileTestFlow key=app_id"]


def test_vector8_persisted_replay_allows_persisted_quality_key() -> None:
    """filter_for_replay allows known replayable quality state."""
    policy = _hostile_policy()

    result = policy.filter_for_replay({"app_validation_status": "passed"}, writer_id=PERSISTED_REPLAY_WRITER)

    assert result == {"app_validation_status": "passed"}


def test_vector8_persisted_replay_allows_ordinary_key() -> None:
    """filter_for_replay with PERSISTED_REPLAY_WRITER allows ordinary mutable state."""
    policy = _hostile_policy()

    result = policy.filter_for_replay({"ordinary_state": "restored"}, writer_id=PERSISTED_REPLAY_WRITER)
    assert result == {"ordinary_state": "restored"}


def test_vector8_persisted_replay_identity_cannot_mutate_live_context() -> None:
    """The stored-state replay identity is not live write authority."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority.rejected"):
        policy.require_can_write("ordinary_state", writer_id=PERSISTED_REPLAY_WRITER)


# ---------------------------------------------------------------------------
# Vector 9: Raw AG2 packets (EV_PACKET context_updates)
# ---------------------------------------------------------------------------

def test_vector9_raw_packet_context_bridge_rejects_routing_key() -> None:
    """AG2 EV_PACKET context_updates via CONTEXT_BRIDGE_WRITER must reject routing keys."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        _authorized_context_updates(
            {"routing_target": "evil"},
            writer_id=CONTEXT_BRIDGE_WRITER,
            context_authority_policy=policy,
        )


def test_vector9_raw_packet_agent_text_rejects_quality_key() -> None:
    """AG2 EV_PACKET context_updates via AGENT_TEXT_WRITER must reject quality keys."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        _authorized_context_updates(
            {"app_validation_status": "evil_pass"},
            writer_id=AGENT_TEXT_WRITER,
            context_authority_policy=policy,
        )


# ---------------------------------------------------------------------------
# Vector 10: Task-batch context updates
# ---------------------------------------------------------------------------

def test_vector10_task_batch_writer_cannot_write_immutable_key() -> None:
    """TASK_BATCH_WRITER cannot write immutable identity keys."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        policy.require_can_write("app_id", writer_id=TASK_BATCH_WRITER)


def test_vector10_task_batch_writer_cannot_write_quality_key() -> None:
    """TASK_BATCH_WRITER cannot write quality-gate keys (not in declared writer_ids)."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        policy.require_can_write("app_validation_status", writer_id=TASK_BATCH_WRITER)


# ---------------------------------------------------------------------------
# Vector 11: Network packet context updates
# ---------------------------------------------------------------------------

def test_vector11_network_packet_rejects_unknown_key() -> None:
    """Network packet context updates via _authorized_context_updates fail-closed on unknown keys."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority.unknown_key"):
        _authorized_context_updates(
            {"completely_unknown_routing_attack": "evil"},
            writer_id=CONTEXT_BRIDGE_WRITER,
            context_authority_policy=policy,
        )


def test_vector11_network_packet_rejects_immutable_key_for_live_user() -> None:
    """Network packet with LIVE_USER_CONTEXT_WRITER cannot write immutable keys."""
    policy = _hostile_policy()

    with pytest.raises(ContextAuthorityError, match="context_authority"):
        _authorized_context_updates(
            {"user_id": "evil"},
            writer_id=LIVE_USER_CONTEXT_WRITER,
            context_authority_policy=policy,
        )


# ---------------------------------------------------------------------------
# Transition graph validation
# ---------------------------------------------------------------------------

def test_transition_graph_rejects_routing_key_with_agent_text_writer_only() -> None:
    """Transition graph compilation rejects routing keys that have only agent_text writer."""
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


# ---------------------------------------------------------------------------
# No second context store (Q13)
# ---------------------------------------------------------------------------

def test_no_second_context_store_in_bridge() -> None:
    """ContextVariablesBridge uses a single _data dict — no shadow store."""
    policy = _hostile_policy()
    bridge = ContextVariablesBridge({"ordinary_state": "draft"}, authority_policy=policy)

    bridge.set("ordinary_state", "updated")
    pending = bridge.consume_context_updates()

    # Only one authoritative view
    assert pending["set"] == {"ordinary_state": "updated"}
    assert bridge.get("ordinary_state") == "updated"
