from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mozaiksai.core.workflow.execution.network_graph import (
    compile_transition_rules_to_graph,
    resolve_next_agent,
)
from mozaiksai.core.workflow.outputs.structured import (
    load_workflow_structured_outputs,
    supports_provider_response_format,
)
from mozaiksai.core.workflow.task_batches import load_task_batches_config
from scripts.run_live_context_expression_task_batch_smoke import (
    _initialize_workflow_root,
    _preflight_context_expression_route,
    _validation_errors,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = ROOT / "factory_app" / "workflows"
WORKFLOW_NAME = "RuntimeContextExpressionTaskBatchSmoke"
WORKFLOW_DIR = WORKFLOWS_ROOT / WORKFLOW_NAME


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _agent_names() -> list[str]:
    agents = _read_yaml(WORKFLOW_DIR / "agents.yaml")["agents"]
    return [str(agent["name"]) for agent in agents]


def test_runtime_context_expression_task_batch_smoke_orchestrator_contract() -> None:
    orchestrator = _read_yaml(WORKFLOW_DIR / "orchestrator.yaml")

    assert orchestrator["workflow_name"] == WORKFLOW_NAME
    assert orchestrator["orchestration_pattern"] == "ag2_network"
    assert orchestrator["initial_agent"] == "TaskPlannerAgent"
    assert orchestrator["human_in_the_loop"] is False


def test_runtime_context_expression_task_batch_smoke_routes_with_ag2_context_expression() -> None:
    transition_graph = _read_yaml(WORKFLOW_DIR / "transition_graph.yaml")
    agent_names = _agent_names()
    graph = compile_transition_rules_to_graph(
        transition_graph["transition_rules"],
        initial_agent_name="TaskPlannerAgent",
        agent_id_by_name={name: name for name in agent_names},
        max_turns=8,
    )

    assert (
        resolve_next_agent(
            graph,
            current_agent_name="TaskPlannerAgent",
            context_variables={
                "task_batch_route_ready": True,
                "routing_mode": "task_batch",
                "selected_capabilities": ["module"],
            },
            agent_name_by_id={name: name for name in agent_names},
            participant_order=agent_names,
        )
        == "SynthesisAgent"
    )
    assert (
        resolve_next_agent(
            graph,
            current_agent_name="TaskPlannerAgent",
            context_variables={
                "task_batch_route_ready": False,
                "routing_mode": "task_batch",
                "selected_capabilities": ["module"],
            },
            agent_name_by_id={name: name for name in agent_names},
            participant_order=agent_names,
        )
        == "terminate"
    )


def test_runtime_context_expression_refs_are_declared_for_agentgenerator_contract() -> None:
    context = _read_yaml(WORKFLOW_DIR / "context_variables.yaml")
    declared = set(context["definitions"])
    planner_vars = set(context["agents"]["TaskPlannerAgent"]["variables"])

    assert {"task_batch_route_ready", "routing_mode", "selected_capabilities"} <= declared
    assert {"task_batch_route_ready", "routing_mode", "selected_capabilities"} <= planner_vars


def test_runtime_context_expression_task_batch_conveyor_contract() -> None:
    config = load_task_batches_config(WORKFLOW_NAME, workflows_root=WORKFLOWS_ROOT)

    assert config is not None
    batch = config.batches[0]
    assert batch.id == "runtime_smoke_tasks"
    assert batch.trigger_agent == "TaskPlannerAgent"
    assert batch.source.kind == "structured_output"
    assert batch.source.path == "DecompositionPlan.tasks"
    assert batch.allowed_execution_agents == [
        "ModuleTaskWorkerAgent",
        "PageTaskWorkerAgent",
        "IntegrationTaskWorkerAgent",
    ]
    assert batch.result.context_key == "runtime_smoke_tasks_results"
    assert batch.result.status_key == "runtime_smoke_tasks_status"


def test_live_runner_preflight_loads_workflow_and_resolves_route() -> None:
    config = _initialize_workflow_root(WORKFLOWS_ROOT, WORKFLOW_NAME)

    assert _preflight_context_expression_route(config) == "SynthesisAgent"


def test_runtime_context_expression_planner_schema_is_strict_and_required() -> None:
    _initialize_workflow_root(WORKFLOWS_ROOT, WORKFLOW_NAME)
    _, registry = load_workflow_structured_outputs(WORKFLOW_NAME)
    planner_model = registry["TaskPlannerAgent"]

    assert supports_provider_response_format(planner_model) == (True, None)
    with pytest.raises(Exception, match="DecompositionPlan"):
        planner_model.model_validate({"agent_message": "planned", "task_batch": {"tasks": []}})


def test_live_runner_validation_accepts_successful_task_batch_payload() -> None:
    errors = _validation_errors(
        result={"run_completed": True, "run_status": 1},
        synthesis_output={
            "task_batch_execution_used": True,
            "all_units_succeeded": True,
            "work_unit_count": 3,
            "max_parallelism": 3,
            "executed_task_ids": ["module", "page", "integration"],
            "result_context_key": "runtime_smoke_tasks_results",
            "failure_count": 0,
        },
        final_context={
            "runtime_smoke_tasks_status": "completed",
            "runtime_smoke_tasks_results": {
                "_meta": {
                    "status": "completed",
                    "task_count": 3,
                    "concurrency": 3,
                    "completed_tasks": ["module", "page", "integration"],
                    "failed_tasks": [],
                    "result_context_key": "runtime_smoke_tasks_results",
                }
            },
        },
        route_target="SynthesisAgent",
    )

    assert errors == []
