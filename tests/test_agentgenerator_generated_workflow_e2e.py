from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from autogen.beta.network import EV_PACKET, Envelope, WorkflowAdapter, WorkflowState
from autogen.beta.network.policies import CHANNEL_STATE_DEP

from mozaiksai.core.workflow.execution.network_graph import (
    compile_transition_rules_to_graph,
    resolve_next_agent,
)
from mozaiksai.core.workflow.task_batches import (
    execute_task_batches_for_trigger,
    load_task_batches_config,
)
from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")
_workflow_converter = import_module_directly(
    "factory_app.workflows.AgentGenerator.tools.workflow_converter"
)


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _ag2_task_context(kwargs: dict[str, Any]) -> dict[str, Any]:
    state = kwargs["dependencies"][CHANNEL_STATE_DEP]
    return dict(state.context_vars)


def _write_agentgenerator_bundle(source_dir: Path, workflow_name: str) -> list[dict[str, Any]]:
    transition_rules = _workflow_converter._normalize_transition_rules(
        [
            {
                "source_agent": "PlannerAgent",
                "target_agent": "ToolRouteAgent",
                "transition_type": "condition",
                "condition_type": "tool_called",
                "tool_name": "route_to_tool_agent",
            },
            {
                "source_agent": "PlannerAgent",
                "target_agent": "user",
                "transition_type": "condition",
                "condition_type": "context_equals",
                "condition_key": "plan_ready",
                "condition_value": False,
                "transition_target": "RevertToUserTarget",
            },
            {
                "source_agent": "PlannerAgent",
                "target_agent": "SynthesisAgent",
                "transition_type": "condition",
                "condition_type": "context_expression",
                "context_expression": (
                    "${plan_ready} and ${complexity} == 'heavy' "
                    "and len(${selected_capabilities}) > 0"
                ),
            },
            {
                "source_agent": "SynthesisAgent",
                "target_agent": "terminate",
                "transition_type": "after_turn",
                "transition_target": "TerminateTarget",
            },
        ]
    )

    _write_yaml(
        source_dir / "orchestrator.yaml",
        {
            "workflow_name": workflow_name,
            "max_turns": 6,
            "human_in_the_loop": False,
            "workflow_startup_mode": "AgentDriven",
            "orchestration_pattern": "ag2_network",
            "initial_message": "Decompose heavy workflow work.",
            "initial_agent": "PlannerAgent",
            "triggers": [{"type": "chat", "description": "Generated workflow smoke"}],
        },
    )
    _write_yaml(
        source_dir / "agents.yaml",
        {
            "agents": [
                {"name": "PlannerAgent", "system_message": "Plan workflow tasks."},
                {"name": "WorkerAgent", "system_message": "Execute one workflow task."},
                {"name": "SynthesisAgent", "system_message": "Synthesize task outputs."},
                {"name": "ToolRouteAgent", "system_message": "Handle explicit routing tools."},
            ]
        },
    )
    _write_yaml(source_dir / "transition_graph.yaml", {"transition_rules": transition_rules})
    _write_yaml(
        source_dir / "context_variables.yaml",
        {
            "definitions": {
                "plan_ready": {"type": "boolean", "source": {"type": "state", "default": False}},
                "complexity": {"type": "string", "source": {"type": "state", "default": "light"}},
                "selected_capabilities": {"type": "list", "source": {"type": "state", "default": []}},
                "workflow_tasks_results": {"type": "object", "source": {"type": "state", "default": {}}},
                "workflow_tasks_status": {"type": "string", "source": {"type": "state", "default": "pending"}},
            },
            "agents": {
                "PlannerAgent": {"variables": ["plan_ready", "complexity", "selected_capabilities"]},
                "SynthesisAgent": {"variables": ["workflow_tasks_results", "workflow_tasks_status"]},
            },
        },
    )
    _write_yaml(
        source_dir / "structured_outputs.yaml",
        {
            "registry": {
                "PlannerAgent": "DecompositionPlan",
                "WorkerAgent": "WorkerOutput",
                "SynthesisAgent": "SynthesisOutput",
                "ToolRouteAgent": None,
            },
            "models": {
                "DecompositionPlan": {
                    "type": "model",
                    "fields": {
                        "tasks": {
                            "type": "list",
                            "items": "DecomposedTask",
                            "description": "Tasks for downstream workflow workers.",
                        }
                    },
                },
                "WorkerOutput": {
                    "type": "model",
                    "fields": {
                        "agent_message": {"type": "str", "description": "Worker summary."},
                        "code_files": {
                            "type": "list",
                            "items": "CodeFile",
                            "description": "Workflow files emitted by the worker.",
                        },
                    },
                },
                "SynthesisOutput": {
                    "type": "model",
                    "fields": {
                        "agent_message": {"type": "str", "description": "Synthesis summary."}
                    },
                },
            },
        },
    )
    _write_yaml(source_dir / "tools.yaml", {"tools": [], "lifecycle_tools": []})
    _write_yaml(source_dir / "ui_config.yaml", {"visual_agents": ["PlannerAgent", "SynthesisAgent"]})
    _write_yaml(source_dir / "middleware.yaml", {"prompt_middleware": []})
    _write_yaml(
        source_dir / "extended_orchestration" / "task_batches.yaml",
        {
            "version": 1,
            "conveyors": [
                {
                    "id": "workflow_tasks",
                    "decomposition_agent": "PlannerAgent",
                    "execution_agents": ["WorkerAgent"],
                    "concurrency": 1,
                    "require_owned_paths": True,
                }
            ],
        },
    )
    return transition_rules


def test_agentgenerator_factory_workflow_loads_cleanly() -> None:
    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path="factory_app/workflows")
    info = manager.get_workflow_info("AgentGenerator")

    assert info is not None
    assert info["status"] == "loaded"
    assert info["error"] is None


@pytest.mark.asyncio
async def test_agentgenerator_generated_workflow_contract_loads_routes_and_executes_conveyor(
    tmp_path: Path,
) -> None:
    workflow_name = "GeneratedWorkflowE2E"
    generated_source = tmp_path / "generated" / "workflows" / "app-1" / "build-1" / workflow_name
    _write_agentgenerator_bundle(generated_source, workflow_name)

    active_workflows_root = tmp_path / "active" / "workflows"
    promotion = _workflow_converter.promote_generated_workflow(generated_source, active_workflows_root)
    workflow_dir = Path(promotion["target_dir"])

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(active_workflows_root))
    info = manager.get_workflow_info(workflow_name)
    config = manager.get_config(workflow_name)

    assert workflow_dir.exists()
    assert info is not None
    assert info["status"] == "loaded"
    loaded_transition_rules = config["transition_graph"]["transition_rules"]
    assert [rule["condition_type"] for rule in loaded_transition_rules[:3]] == [
        "tool_called",
        "context_equals",
        "context_expression",
    ]
    assert "condition" not in loaded_transition_rules[0]

    agent_names = [agent["name"] for agent in config["agents"]["agents"].values()]
    graph = compile_transition_rules_to_graph(
        config["transition_graph"]["transition_rules"],
        initial_agent_name=config["initial_agent"],
        agent_id_by_name={name: name for name in agent_names},
        max_turns=config["max_turns"],
    )

    assert (
        resolve_next_agent(
            graph,
            current_agent_name="PlannerAgent",
            context_variables={
                "plan_ready": False,
                "complexity": "light",
                "selected_capabilities": [],
            },
            agent_name_by_id={name: name for name in agent_names},
            participant_order=agent_names,
        )
        == "user"
    )
    assert (
        resolve_next_agent(
            graph,
            current_agent_name="PlannerAgent",
            context_variables={
                "plan_ready": True,
                "complexity": "heavy",
                "selected_capabilities": ["task_conveyor"],
            },
            agent_name_by_id={name: name for name in agent_names},
            participant_order=agent_names,
        )
        == "SynthesisAgent"
    )

    tool_state = WorkflowState(
        participant_order=agent_names,
        expected_next_speaker="PlannerAgent",
        last_speaker_id="PlannerAgent",
        turn_count=1,
        creator_id="user",
        graph_data=graph.to_dict(),
        context_vars={
            "plan_ready": True,
            "complexity": "heavy",
            "selected_capabilities": ["task_conveyor"],
        },
    )
    tool_envelope = Envelope(
        channel_id="agentgenerator-e2e",
        sender_id="PlannerAgent",
        audience=None,
        event_type=EV_PACKET,
        event_data={"routing": {"tool": "route_to_tool_agent"}},
    )

    assert WorkflowAdapter().fold(tool_envelope, tool_state).expected_next_speaker == "ToolRouteAgent"

    task_batches = load_task_batches_config(workflow_name, workflows_root=active_workflows_root)
    assert task_batches is not None
    assert task_batches.batches[0].id == "workflow_tasks"

    seen_worker_contexts: list[dict[str, Any]] = []

    class _WorkerAgent:
        async def ask(self, message: str, **kwargs: Any) -> SimpleNamespace:
            variables = _ag2_task_context(kwargs)
            seen_worker_contexts.append({"message": message, "variables": variables})
            return SimpleNamespace(
                body=json.dumps(
                    {
                        "agent_message": "Worker emitted the generated workflow tool file.",
                        "code_files": [
                            {
                                "filename": "tools/generated_worker.py",
                                "content": "async def run():\n    return {'ok': True}\n",
                            }
                        ],
                    }
                )
            )

    context_variables = {
        "plan_ready": True,
        "complexity": "heavy",
        "selected_capabilities": ["task_conveyor"],
    }
    task_results = await execute_task_batches_for_trigger(
        workflow_name=workflow_name,
        trigger_agent="PlannerAgent",
        batches_config=task_batches,
        agents={"WorkerAgent": _WorkerAgent()},
        context_variables=context_variables,
        structured_output={
            "DecompositionPlan": {
                "tasks": [
                    {
                        "task_id": "generated_worker_tool",
                        "execution_agent": "WorkerAgent",
                        "task_prompt": "Generate the workflow-local worker tool.",
                        "owned_paths": ["tools/generated_worker.py"],
                    }
                ]
            }
        },
        chat_id="chat-1",
        app_id="app-1",
        user_id="user-1",
        fresh_agents_per_task=False,
    )

    assert task_results["workflow_tasks"]["status"] == "completed"
    assert context_variables["workflow_tasks_status"] == "completed"
    assert context_variables["workflow_tasks_results"]["generated_worker_tool"]["code_files"][0] == {
        "filename": "tools/generated_worker.py",
        "content": "async def run():\n    return {'ok': True}\n",
    }
    assert seen_worker_contexts[0]["message"].startswith("Generate the workflow-local worker tool.")
    assert "[TASK BATCH CONTEXT]" in seen_worker_contexts[0]["message"]
    worker_variables = seen_worker_contexts[0]["variables"]
    assert worker_variables["current_task_id"] == "generated_worker_tool"
    assert worker_variables["current_task"]["owned_paths"] == ["tools/generated_worker.py"]
    assert worker_variables["workflow_tasks_status"] == "running"
