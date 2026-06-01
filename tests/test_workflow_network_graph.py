from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from autogen.beta.network import TransitionGraph

from mozaiksai.core.workflow.execution.network_graph import (
    WorkflowGraphCompileError,
    compile_handoffs_to_transition_graph,
    evaluate_context_expression,
    resolve_next_agent,
)


def test_context_expression_supports_boolean_comparisons_and_membership():
    context = {
        "ready": True,
        "status": "passed",
        "adoption_level": "ecosystem",
    }

    assert evaluate_context_expression("${ready} == true", context) is True
    assert evaluate_context_expression("${status} != \"failed\"", context) is True
    assert evaluate_context_expression(
        "${ready} == true and ${adoption_level} in [\"ecosystem\", \"embed\"]",
        context,
    ) is True


def test_context_expression_rejects_non_deterministic_python():
    with pytest.raises(WorkflowGraphCompileError):
        evaluate_context_expression("__import__('os').system('echo nope')", {})


def test_handoffs_compile_to_serializable_ag2_transition_graph():
    graph = compile_handoffs_to_transition_graph(
        [
            {
                "source_agent": "PlannerAgent",
                "target_agent": "ReviewAgent",
                "handoff_type": "condition",
                "condition_type": "expression",
                "condition": "${plan_ready} == true",
            },
            {
                "source_agent": "ReviewAgent",
                "target_agent": "user",
                "handoff_type": "after_work",
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
    graph = compile_handoffs_to_transition_graph(
        [
            {
                "source_agent": "PlannerAgent",
                "target_agent": "ReviewAgent",
                "handoff_type": "condition",
                "condition_type": "expression",
                "condition": "${plan_ready} == true",
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


def test_llm_handoffs_are_rejected():
    with pytest.raises(WorkflowGraphCompileError):
        compile_handoffs_to_transition_graph(
            [
                {
                    "source_agent": "user",
                    "target_agent": "PlannerAgent",
                    "handoff_type": "condition",
                    "condition_type": "string_llm",
                    "condition": "When the user wants changes.",
                }
            ],
            initial_agent_name="PlannerAgent",
            agent_id_by_name={"PlannerAgent": "PlannerAgent"},
        )


def test_factory_workflow_handoffs_compile_to_ag2_network_graphs():
    workflow_root = Path("factory_app/workflows")
    handoff_files = sorted(workflow_root.glob("*/handoffs.yaml"))
    assert handoff_files

    for handoffs_path in handoff_files:
        workflow_dir = handoffs_path.parent
        handoffs = yaml.safe_load(handoffs_path.read_text(encoding="utf-8")) or {}
        orchestrator = yaml.safe_load((workflow_dir / "orchestrator.yaml").read_text(encoding="utf-8")) or {}
        agents_raw = yaml.safe_load((workflow_dir / "agents.yaml").read_text(encoding="utf-8")) or {}
        agent_names = [
            str(agent.get("name")).strip()
            for agent in agents_raw.get("agents", [])
            if isinstance(agent, dict) and str(agent.get("name") or "").strip()
        ]
        initial_agent = str(orchestrator.get("initial_agent") or (agent_names[0] if agent_names else "user"))

        graph = compile_handoffs_to_transition_graph(
            handoffs.get("handoff_rules", []),
            initial_agent_name=initial_agent,
            agent_id_by_name={name: name for name in agent_names},
            max_turns=orchestrator.get("max_turns"),
        )

        assert isinstance(graph, TransitionGraph), handoffs_path
