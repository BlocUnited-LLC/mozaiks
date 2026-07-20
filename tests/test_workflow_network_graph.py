from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ag2.network import (
    EV_PACKET,
    Envelope,
    TransitionGraph,
    WorkflowAdapter,
    WorkflowState,
)

from mozaiksai.core.workflow.agents.transition_graph import wire_transition_graph_with_debugging
from mozaiksai.core.workflow.execution.network_graph import (
    WorkflowGraphCompileError,
    compile_transition_rules_to_graph,
    resolve_next_agent,
)
from mozaiksai.core.workflow.workflow_manager import workflow_manager


def test_transition_graph_uses_only_canonical_terminate_literal():
    graph = compile_transition_rules_to_graph(
        [
            {
                "source_agent": "FinalAgent",
                "target_agent": "terminate",
                "transition_type": "after_turn",
                "transition_target": "TerminateTarget",
            }
        ],
        initial_agent_name="FinalAgent",
        agent_id_by_name={"FinalAgent": "FinalAgent"},
        max_turns=4,
    )

    assert (
        resolve_next_agent(
            graph,
            current_agent_name="FinalAgent",
            context_variables={},
        )
        == "terminate"
    )

    with pytest.raises(WorkflowGraphCompileError):
        compile_transition_rules_to_graph(
            [
                {
                    "source_agent": "FinalAgent",
                    "target_agent": "stop",
                    "transition_type": "after_turn",
                }
            ],
            initial_agent_name="FinalAgent",
            agent_id_by_name={"FinalAgent": "FinalAgent"},
            max_turns=4,
        )


def test_transition_rules_compile_to_serializable_ag2_transition_graph():
    graph = compile_transition_rules_to_graph(
        [
            {
                "source_agent": "PlannerAgent",
                "target_agent": "ReviewAgent",
                "transition_type": "condition",
                "condition_type": "context_equals",
                "condition_key": "plan_ready",
                "condition_value": True,
            },
            {
                "source_agent": "ReviewAgent",
                "target_agent": "user",
                "transition_type": "after_turn",
            },
        ],
        initial_agent_name="PlannerAgent",
        agent_id_by_name={"PlannerAgent": "PlannerAgent", "ReviewAgent": "ReviewAgent"},
        max_turns=4,
    )

    assert isinstance(TransitionGraph.loads(graph.to_dict()), TransitionGraph)
    assert (
        resolve_next_agent(
            graph,
            current_agent_name="PlannerAgent",
            context_variables={"plan_ready": True},
        )
        == "ReviewAgent"
    )
    assert (
        resolve_next_agent(
            graph,
            current_agent_name="ReviewAgent",
            context_variables={"plan_ready": True},
        )
        == "user"
    )


def test_resolve_next_agent_supports_distinct_ag2_agent_ids():
    graph = compile_transition_rules_to_graph(
        [
            {
                "source_agent": "PlannerAgent",
                "target_agent": "ReviewAgent",
                "transition_type": "condition",
                "condition_type": "context_equals",
                "condition_key": "plan_ready",
                "condition_value": True,
            }
        ],
        initial_agent_name="PlannerAgent",
        agent_id_by_name={"PlannerAgent": "agent-planner", "ReviewAgent": "agent-review"},
    )

    assert (
        resolve_next_agent(
            graph,
            current_agent_name="PlannerAgent",
            context_variables={"plan_ready": True},
            agent_name_by_id={"agent-planner": "PlannerAgent", "agent-review": "ReviewAgent"},
            participant_order=["agent-planner", "agent-review"],
        )
        == "ReviewAgent"
    )


def test_context_condition_is_source_scoped():
    graph = compile_transition_rules_to_graph(
        [
            {
                "source_agent": "PlannerAgent",
                "target_agent": "ReviewAgent",
                "transition_type": "condition",
                "condition_type": "context_equals",
                "condition_key": "plan_ready",
                "condition_value": True,
            }
        ],
        initial_agent_name="PlannerAgent",
        agent_id_by_name={
            "PlannerAgent": "PlannerAgent",
            "ReviewAgent": "ReviewAgent",
            "WorkerAgent": "WorkerAgent",
        },
    )

    assert (
        resolve_next_agent(
            graph,
            current_agent_name="WorkerAgent",
            context_variables={"plan_ready": True},
            agent_name_by_id={
                "PlannerAgent": "PlannerAgent",
                "ReviewAgent": "ReviewAgent",
                "WorkerAgent": "WorkerAgent",
            },
            participant_order=["PlannerAgent", "ReviewAgent", "WorkerAgent"],
        )
        == "terminate"
    )


def test_context_expression_condition_uses_ag2_context_expression_and_is_source_scoped():
    graph = compile_transition_rules_to_graph(
        [
            {
                "source_agent": "PlannerAgent",
                "target_agent": "ReviewAgent",
                "transition_type": "condition",
                "condition_type": "context_expression",
                "context_expression": "${route} == 'review' and len(${pending_items}) > 0",
            }
        ],
        initial_agent_name="PlannerAgent",
        agent_id_by_name={
            "PlannerAgent": "PlannerAgent",
            "ReviewAgent": "ReviewAgent",
            "WorkerAgent": "WorkerAgent",
        },
    )

    assert isinstance(TransitionGraph.loads(graph.to_dict()), TransitionGraph)
    assert (
        resolve_next_agent(
            graph,
            current_agent_name="PlannerAgent",
            context_variables={"route": "review", "pending_items": ["a"]},
            agent_name_by_id={
                "PlannerAgent": "PlannerAgent",
                "ReviewAgent": "ReviewAgent",
                "WorkerAgent": "WorkerAgent",
            },
            participant_order=["PlannerAgent", "ReviewAgent", "WorkerAgent"],
        )
        == "ReviewAgent"
    )
    assert (
        resolve_next_agent(
            graph,
            current_agent_name="WorkerAgent",
            context_variables={"route": "review", "pending_items": ["a"]},
            agent_name_by_id={
                "PlannerAgent": "PlannerAgent",
                "ReviewAgent": "ReviewAgent",
                "WorkerAgent": "WorkerAgent",
            },
            participant_order=["PlannerAgent", "ReviewAgent", "WorkerAgent"],
        )
        == "terminate"
    )


def test_tool_called_condition_uses_ag2_routing_tool_packet():
    graph = compile_transition_rules_to_graph(
        [
            {
                "source_agent": "PlannerAgent",
                "target_agent": "ReviewAgent",
                "transition_type": "condition",
                "condition_type": "tool_called",
                "tool_name": "route_to_review",
            }
        ],
        initial_agent_name="PlannerAgent",
        agent_id_by_name={"PlannerAgent": "PlannerAgent", "ReviewAgent": "ReviewAgent"},
    )
    state = WorkflowState(
        participant_order=["PlannerAgent", "ReviewAgent"],
        expected_next_speaker="PlannerAgent",
        last_speaker_id="PlannerAgent",
        turn_count=1,
        creator_id="user",
        graph_data=graph.to_dict(),
        context_vars={},
    )
    envelope = Envelope(
        channel_id="mozaiks-local-routing",
        sender_id="PlannerAgent",
        audience=None,
        event_type=EV_PACKET,
        event_data={"routing": {"tool": "route_to_review"}},
    )

    next_state = WorkflowAdapter().fold(envelope, state)

    assert next_state.expected_next_speaker == "ReviewAgent"


def test_llm_transition_conditions_are_rejected():
    with pytest.raises(WorkflowGraphCompileError):
        compile_transition_rules_to_graph(
            [
                {
                    "source_agent": "user",
                    "target_agent": "PlannerAgent",
                    "transition_type": "condition",
                    "condition_type": "string_llm",
                    "condition": "When the user wants changes.",
                }
            ],
            initial_agent_name="PlannerAgent",
            agent_id_by_name={"PlannerAgent": "PlannerAgent"},
        )


def test_expression_transition_conditions_are_rejected():
    with pytest.raises(WorkflowGraphCompileError):
        compile_transition_rules_to_graph(
            [
                {
                    "source_agent": "user",
                    "target_agent": "PlannerAgent",
                    "transition_type": "condition",
                    "condition_type": "expression",
                    "condition": "${route} == 'plan'",
                }
            ],
            initial_agent_name="PlannerAgent",
            agent_id_by_name={"PlannerAgent": "PlannerAgent"},
        )


def test_factory_transition_graphs_contain_no_stale_condition_field():
    """No transition rule may use the removed `condition` string field.

    The compiler raises WorkflowGraphCompileError at runtime if it finds the field;
    this scan catches authoring mistakes before compilation so the error is
    surfaced at test time with a precise location.
    """
    workflow_root = Path("factory_app/workflows")
    transition_files = sorted(workflow_root.glob("*/transition_graph.yaml"))
    assert transition_files

    violations: list[str] = []
    for transition_path in transition_files:
        data = yaml.safe_load(transition_path.read_text(encoding="utf-8")) or {}
        for rule in data.get("transition_rules", []):
            if "condition" in rule:
                violations.append(
                    f"{transition_path.parent.name}: "
                    f"{rule.get('source_agent')!r} -> {rule.get('target_agent')!r} "
                    "uses removed 'condition' field"
                )

    assert not violations, "\n".join(violations)


def test_factory_transition_graph_condition_rules_use_canonical_fields():
    """All condition-type rules must declare condition_type and the matching sub-fields.

    context_equals requires condition_key + condition_value.
    tool_called requires tool_name.
    Non-deterministic types (expression, llm, string_llm) are banned.
    """
    workflow_root = Path("factory_app/workflows")
    transition_files = sorted(workflow_root.glob("*/transition_graph.yaml"))

    violations: list[str] = []
    for transition_path in transition_files:
        data = yaml.safe_load(transition_path.read_text(encoding="utf-8")) or {}
        for rule in data.get("transition_rules", []):
            if rule.get("transition_type") != "condition":
                continue
            condition_type = rule.get("condition_type", "")
            label = (
                f"{transition_path.parent.name}: "
                f"{rule.get('source_agent')!r} -> {rule.get('target_agent')!r}"
            )
            if not condition_type:
                violations.append(f"{label}: missing condition_type")
                continue
            if condition_type in {"expression", "llm", "string_llm"}:
                violations.append(f"{label}: non-deterministic condition_type={condition_type!r}")
                continue
            if condition_type == "context_equals":
                if not rule.get("condition_key"):
                    violations.append(f"{label}: context_equals missing condition_key")
                if "condition_value" not in rule:
                    violations.append(f"{label}: context_equals missing condition_value")
            elif condition_type == "context_expression":
                if not rule.get("context_expression"):
                    violations.append(f"{label}: context_expression missing context_expression")
            elif condition_type == "tool_called":
                if not rule.get("tool_name"):
                    violations.append(f"{label}: tool_called missing tool_name")
            else:
                violations.append(f"{label}: unknown condition_type={condition_type!r}")

    assert not violations, "\n".join(violations)


def test_factory_workflow_transition_rules_compile_to_ag2_network_graphs():
    workflow_root = Path("factory_app/workflows")
    transition_files = sorted(workflow_root.glob("*/transition_graph.yaml"))
    assert transition_files

    for transition_path in transition_files:
        workflow_dir = transition_path.parent
        transitions = yaml.safe_load(transition_path.read_text(encoding="utf-8")) or {}
        orchestrator = yaml.safe_load((workflow_dir / "orchestrator.yaml").read_text(encoding="utf-8")) or {}
        agents_raw = yaml.safe_load((workflow_dir / "agents.yaml").read_text(encoding="utf-8")) or {}
        agent_names = [
            str(agent.get("name")).strip()
            for agent in agents_raw.get("agents", [])
            if isinstance(agent, dict) and str(agent.get("name") or "").strip()
        ]
        initial_agent = str(orchestrator.get("initial_agent") or (agent_names[0] if agent_names else "user"))

        graph = compile_transition_rules_to_graph(
            transitions.get("transition_rules", []),
            initial_agent_name=initial_agent,
            agent_id_by_name={name: name for name in agent_names},
            max_turns=orchestrator.get("max_turns"),
        )

        assert isinstance(graph, TransitionGraph), transition_path


def test_appgenerator_validation_routes_repair_context_before_user_fallback():
    workflow_dir = Path("factory_app/workflows/AppGenerator")
    transitions = yaml.safe_load((workflow_dir / "transition_graph.yaml").read_text(encoding="utf-8")) or {}
    orchestrator = yaml.safe_load((workflow_dir / "orchestrator.yaml").read_text(encoding="utf-8")) or {}
    agents_raw = yaml.safe_load((workflow_dir / "agents.yaml").read_text(encoding="utf-8")) or {}
    agent_names = [
        str(agent.get("name")).strip()
        for agent in agents_raw.get("agents", [])
        if isinstance(agent, dict) and str(agent.get("name") or "").strip()
    ]
    agent_name_by_id = {name: name for name in agent_names}
    participant_order = [*agent_names, "user"]
    graph = compile_transition_rules_to_graph(
        transitions.get("transition_rules", []),
        initial_agent_name=str(orchestrator.get("initial_agent") or agent_names[0]),
        agent_id_by_name=agent_name_by_id,
        max_turns=orchestrator.get("max_turns"),
    )

    def route(context_variables: dict) -> str | None:
        return resolve_next_agent(
            graph,
            current_agent_name="AppValidationAgent",
            context_variables=context_variables,
            agent_name_by_id=agent_name_by_id,
            participant_order=participant_order,
        )

    assert route({"workflow_integration_repair_status": "needs_revision"}) == "ConfigMiddlewareAgent"
    assert route({"bundle_repair_target": "AppSchemaAgent"}) == "AppSchemaAgent"
    assert route({"bundle_repair_target": "ConfigMiddlewareAgent"}) == "ConfigMiddlewareAgent"
    assert route({"bundle_repair_target": "ServiceAgent"}) == "ServiceAgent"
    assert route({"bundle_repair_target": "FrontendStubAgent"}) == "FrontendStubAgent"
    assert route({"bundle_repair_status": "blocked"}) == "user"
    assert route({"workflow_integration_repair_status": "blocked"}) == "user"
    assert route({"app_validation_status": "failed"}) == "user"
    assert route({"integration_tests_passed": True}) == "InfraScaffoldAgent"
    assert route({}) == "user"


def test_transition_graph_validator_accepts_user_as_special_source(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        workflow_manager,
        "get_config",
        lambda _workflow_name: {
            "transition_graph": {
                "transition_rules": [
                    {
                        "source_agent": "user",
                        "target_agent": "PlannerAgent",
                        "transition_type": "condition",
                        "condition_type": "context_equals",
                        "condition_key": "plan_ready",
                        "condition_value": False,
                    }
                ]
            },
            "initial_agent": "PlannerAgent",
        },
    )

    summary = wire_transition_graph_with_debugging(
        "UserSourceWorkflow",
        {"PlannerAgent": object()},
    )

    assert summary["missing_source_agents"] == []
    assert summary["missing_target_agents"] == []
    assert summary["errors"] == []

