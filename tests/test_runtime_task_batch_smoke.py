from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from ag2.network.policies import CHANNEL_STATE_DEP

from mozaiksai.core.workflow.execution.network_graph import (
    compile_transition_rules_to_graph,
    resolve_next_agent,
)
from mozaiksai.core.workflow.task_batches import (
    execute_task_batches_for_trigger,
    load_task_batches_config,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = ROOT / "factory_app" / "workflows"
WORKFLOW_DIR = WORKFLOWS_ROOT / "RuntimeTaskBatchSmoke"


def _ag2_task_context(kwargs: dict) -> dict:
    state = kwargs["dependencies"][CHANNEL_STATE_DEP]
    return dict(state.context_vars)


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _agent_names() -> list[str]:
    agents = _read_yaml(WORKFLOW_DIR / "agents.yaml")["agents"]
    return [str(agent["name"]) for agent in agents]


def test_runtime_task_batch_smoke_orchestrator_contract() -> None:
    data = _read_yaml(WORKFLOW_DIR / "orchestrator.yaml")

    assert data["workflow_name"] == "RuntimeTaskBatchSmoke"
    assert data["human_in_the_loop"] is False
    assert data["initial_agent"] == "TaskPlannerAgent"
    assert data["orchestration_pattern"] == "ag2_network"


def test_runtime_task_batch_smoke_structured_outputs_contract() -> None:
    data = _read_yaml(WORKFLOW_DIR / "structured_outputs.yaml")

    assert data["registry"]["TaskPlannerAgent"] == "RuntimeTaskBatchPlanOutput"
    assert data["registry"]["ModuleTaskWorkerAgent"] == "RuntimeTaskBatchWorkerOutput"
    assert data["registry"]["PageTaskWorkerAgent"] == "RuntimeTaskBatchWorkerOutput"
    assert data["registry"]["IntegrationTaskWorkerAgent"] == "RuntimeTaskBatchWorkerOutput"
    assert data["registry"]["SynthesisAgent"] == "RuntimeTaskBatchSmokeResult"
    models = data["models"]
    assert "DecompositionPlan" in models
    assert "DecomposedTask" in models
    assert "RuntimeTaskBatchWorkerOutput" in models
    assert "RuntimeTaskBatchSmokeResult" in models
    task_fields = models["DecomposedTask"]["fields"]
    assert task_fields["kind"]["values"] == ["module", "page", "integration"]
    assert task_fields["execution_agent"]["values"] == [
        "ModuleTaskWorkerAgent",
        "PageTaskWorkerAgent",
        "IntegrationTaskWorkerAgent",
    ]
    worker_fields = models["RuntimeTaskBatchWorkerOutput"]["fields"]
    assert worker_fields["kind"]["values"] == ["module", "page", "integration"]


def test_runtime_task_batch_smoke_declares_task_batches_yaml() -> None:
    config = load_task_batches_config("RuntimeTaskBatchSmoke", workflows_root=WORKFLOWS_ROOT)

    assert config is not None
    batch = config.batches[0]
    assert batch.id == "runtime_smoke_tasks"
    assert batch.trigger_agent == "TaskPlannerAgent"
    assert batch.source.kind == "structured_output"
    assert batch.source.path == "DecompositionPlan.tasks"
    assert batch.source.task_model == "DecomposedTask"
    assert batch.worker.agent_field == "execution_agent"
    assert batch.worker.prompt_field == "task_prompt"
    assert batch.allowed_execution_agents == [
        "ModuleTaskWorkerAgent",
        "PageTaskWorkerAgent",
        "IntegrationTaskWorkerAgent",
    ]
    assert batch.result.context_key == "runtime_smoke_tasks_results"
    assert batch.result.status_key == "runtime_smoke_tasks_status"


def test_runtime_task_batch_smoke_context_variables_expose_real_worker_agents() -> None:
    data = _read_yaml(WORKFLOW_DIR / "context_variables.yaml")
    definitions = set(data["definitions"])
    agents = data["agents"]

    assert {"task_batch_route_ready", "task_batch_route_mode"} <= definitions
    assert "TaskWorkerAgent" not in agents
    assert set(agents["TaskPlannerAgent"]["variables"]) == {
        "task_batch_route_ready",
        "task_batch_route_mode",
    }
    expected_worker_vars = {
        "task_run_mode",
        "current_task_batch_id",
        "current_task_id",
        "current_task",
    }
    assert set(agents["ModuleTaskWorkerAgent"]["variables"]) == expected_worker_vars
    assert set(agents["PageTaskWorkerAgent"]["variables"]) == expected_worker_vars
    assert set(agents["IntegrationTaskWorkerAgent"]["variables"]) == expected_worker_vars
    assert {
        "runtime_smoke_tasks_results",
        "runtime_smoke_tasks_status",
        "task_batch_route_mode",
    } <= set(agents["SynthesisAgent"]["variables"])


def test_runtime_task_batch_smoke_routes_with_context_equals_then_after_turn() -> None:
    data = _read_yaml(WORKFLOW_DIR / "transition_graph.yaml")
    agent_names = _agent_names()
    planner_rule = data["transition_rules"][0]
    synthesis_rule = data["transition_rules"][1]

    assert planner_rule == {
        "source_agent": "TaskPlannerAgent",
        "target_agent": "SynthesisAgent",
        "transition_type": "condition",
        "condition_type": "context_equals",
        "condition_key": "task_batch_route_ready",
        "condition_value": True,
    }
    assert synthesis_rule == {
        "source_agent": "SynthesisAgent",
        "target_agent": "terminate",
        "transition_type": "after_turn",
        "transition_target": "TerminateTarget",
    }

    graph = compile_transition_rules_to_graph(
        data["transition_rules"],
        initial_agent_name="TaskPlannerAgent",
        agent_id_by_name={name: name for name in agent_names},
        max_turns=8,
    )
    assert (
        resolve_next_agent(
            graph,
            current_agent_name="TaskPlannerAgent",
            context_variables={"task_batch_route_ready": True},
            agent_name_by_id={name: name for name in agent_names},
            participant_order=agent_names,
        )
        == "SynthesisAgent"
    )
    assert (
        resolve_next_agent(
            graph,
            current_agent_name="TaskPlannerAgent",
            context_variables={"task_batch_route_ready": False},
            agent_name_by_id={name: name for name in agent_names},
            participant_order=agent_names,
        )
        == "terminate"
    )
    assert (
        resolve_next_agent(
            graph,
            current_agent_name="SynthesisAgent",
            context_variables={"task_batch_route_ready": True},
            agent_name_by_id={name: name for name in agent_names},
            participant_order=agent_names,
        )
        == "terminate"
    )


def test_runtime_task_batch_smoke_has_multiple_execution_agents_under_ag2_network() -> None:
    orchestrator = _read_yaml(WORKFLOW_DIR / "orchestrator.yaml")
    agents = _read_yaml(WORKFLOW_DIR / "agents.yaml")["agents"]
    names = {agent["name"] for agent in agents}

    assert orchestrator["orchestration_pattern"] == "ag2_network"
    assert {
        "ModuleTaskWorkerAgent",
        "PageTaskWorkerAgent",
        "IntegrationTaskWorkerAgent",
    } <= names


def test_runtime_task_batch_synthesis_copies_executor_meta_exactly() -> None:
    data = _read_yaml(WORKFLOW_DIR / "agents.yaml")
    synthesis = next(agent for agent in data["agents"] if agent["name"] == "SynthesisAgent")
    instructions = "\n".join(
        str(section.get("content") or "")
        for section in synthesis["prompt_sections"]
    )

    assert "work_unit_count` MUST equal `runtime_smoke_tasks_results._meta.task_count" in instructions
    assert "executed_task_ids` MUST equal `runtime_smoke_tasks_results._meta.completed_tasks" in instructions
    assert "result_context_key` MUST equal `runtime_smoke_tasks_results._meta.result_context_key" in instructions
    assert "Do not invent task ids" in instructions


def test_runtime_task_batch_synthesis_middleware_injects_exact_executor_summary() -> None:
    middleware = _read_yaml(WORKFLOW_DIR / "middleware.yaml")
    assert middleware["prompt_middleware"][0]["function"] == "inject_task_batch_synthesis_context"
    assert middleware["prompt_middleware"][1] == {
        "agent": "TaskPlannerAgent",
        "filename": "hook_task_batch_route_context.py",
        "function": "inject_task_batch_route_context",
    }

    module = _load_module(
        WORKFLOW_DIR / "tools" / "hook_task_batch_synthesis.py",
        "tests.runtime_task_batch_synthesis_hook",
    )

    class _Context:
        def __init__(self) -> None:
            self.data = {
                "runtime_smoke_tasks_status": "completed",
                "runtime_smoke_tasks_results": {
                    "profiles": {"kind": "module", "summary": "Profiles done."},
                    "feed": {"kind": "service", "summary": "Feed done."},
                    "_meta": {
                        "status": "completed",
                        "task_count": 2,
                        "concurrency": 4,
                        "completed_tasks": ["profiles", "feed"],
                        "failed_tasks": [],
                        "result_context_key": "runtime_smoke_tasks_results",
                    },
                },
            }

        def get(self, key: str, default=None):
            return self.data.get(key, default)

    class _Agent:
        name = "SynthesisAgent"

        def __init__(self) -> None:
            self.context_variables = _Context()
            self.system_message = "base"

        def update_system_message(self, message: str) -> None:
            self.system_message = message

    agent = _Agent()
    module.inject_task_batch_synthesis_context(agent, [])

    assert "[DETERMINISTIC TASK BATCH SYNTHESIS]" in agent.system_message
    assert '"work_unit_count": 2' in agent.system_message
    assert '"max_parallelism": 4' in agent.system_message
    assert '"executed_task_ids": [' in agent.system_message
    assert '"profiles"' in agent.system_message
    assert '"feed"' in agent.system_message
    assert '"result_context_key": "runtime_smoke_tasks_results"' in agent.system_message


def test_runtime_task_batch_route_middleware_injects_planner_context() -> None:
    module = _load_module(
        WORKFLOW_DIR / "tools" / "hook_task_batch_route_context.py",
        "tests.runtime_task_batch_route_hook",
    )

    class _Context:
        def get(self, key: str, default=None):
            data = {
                "task_batch_route_ready": True,
                "task_batch_route_mode": "task_batch",
            }
            return data.get(key, default)

    class _Agent:
        name = "TaskPlannerAgent"

        def __init__(self) -> None:
            self.context_variables = _Context()
            self.system_message = "base"

        def update_system_message(self, message: str) -> None:
            self.system_message = message

    agent = _Agent()
    module.inject_task_batch_route_context(agent, [])

    assert "[TASK BATCH ROUTE CONTEXT]" in agent.system_message
    assert "task_batch_route_ready: True" in agent.system_message
    assert "task_batch_route_mode: task_batch" in agent.system_message


async def _run_runtime_task_batch_smoke_multi_agent_fixture() -> tuple[dict, dict[str, dict]]:
    config = load_task_batches_config("RuntimeTaskBatchSmoke", workflows_root=WORKFLOWS_ROOT)
    assert config is not None
    seen: dict[str, dict] = {}

    class _FakeAgent:
        def __init__(self, name: str) -> None:
            self.name = name

        async def ask(self, message, **kwargs):
            variables = _ag2_task_context(kwargs)
            task_id = variables["current_task_id"]
            seen[task_id] = {"agent": self.name, "variables": variables, "message": message}
            return SimpleNamespace(
                body=json.dumps(
                    {
                        "agent_message": f"{self.name} completed {task_id}.",
                        "task_id": task_id,
                        "kind": variables["current_task"].get("kind") or self.name,
                        "summary": f"{self.name} used {len(variables['dependency_task_outputs'])} dependency outputs.",
                        "deliverables": [f"{task_id} deliverable"],
                        "owned_paths": variables["current_task"].get("owned_paths") or [],
                        "code_files": None,
                    }
                )
            )

    context: dict = {}
    await execute_task_batches_for_trigger(
        workflow_name="RuntimeTaskBatchSmoke",
        trigger_agent="TaskPlannerAgent",
        batches_config=config,
        agents={
            "ModuleTaskWorkerAgent": _FakeAgent("ModuleTaskWorkerAgent"),
            "PageTaskWorkerAgent": _FakeAgent("PageTaskWorkerAgent"),
            "IntegrationTaskWorkerAgent": _FakeAgent("IntegrationTaskWorkerAgent"),
        },
        context_variables=context,
        structured_output={
            "DecompositionPlan": {
                "tasks": [
                    {
                        "task_id": "module_contract",
                        "kind": "module",
                        "execution_agent": "ModuleTaskWorkerAgent",
                        "task_prompt": "Create the module contract.",
                        "owned_paths": ["modules/campaigns/module.yaml"],
                    },
                    {
                        "task_id": "integration_adapter",
                        "kind": "integration",
                        "execution_agent": "IntegrationTaskWorkerAgent",
                        "task_prompt": "Create the integration adapter from the module contract.",
                        "depends_on": ["module_contract"],
                        "owned_paths": ["services/integrations/campaigns_client.py"],
                    },
                    {
                        "task_id": "campaign_page",
                        "kind": "page",
                        "execution_agent": "PageTaskWorkerAgent",
                        "task_prompt": "Create the campaign page from module and integration outputs.",
                        "depends_on": ["module_contract", "integration_adapter"],
                        "owned_paths": ["ui/pages/campaigns.yaml"],
                    },
                ]
            }
        },
        fresh_agents_per_task=False,
    )
    return context, seen


async def _run_runtime_task_batch_smoke_parallel_fixture() -> tuple[dict, SimpleNamespace]:
    config = load_task_batches_config("RuntimeTaskBatchSmoke", workflows_root=WORKFLOWS_ROOT)
    assert config is not None
    state = SimpleNamespace(active=0, max_active=0, task_ids=[])

    class _ParallelAgent:
        def __init__(self, name: str) -> None:
            self.name = name

        async def ask(self, message, **kwargs):
            variables = _ag2_task_context(kwargs)
            task_id = variables["current_task_id"]
            state.active += 1
            state.max_active = max(state.max_active, state.active)
            state.task_ids.append(task_id)
            try:
                await asyncio.sleep(0.05)
                return SimpleNamespace(
                    body=json.dumps(
                        {
                            "agent_message": f"{self.name} completed {task_id}.",
                            "task_id": task_id,
                            "kind": variables["current_task"].get("kind") or self.name,
                            "summary": f"{self.name} completed {message}.",
                            "deliverables": [f"{task_id} deliverable"],
                            "owned_paths": variables["current_task"].get("owned_paths") or [],
                            "code_files": None,
                        }
                    )
                )
            finally:
                state.active -= 1

    context: dict = {}
    await execute_task_batches_for_trigger(
        workflow_name="RuntimeTaskBatchSmoke",
        trigger_agent="TaskPlannerAgent",
        batches_config=config,
        agents={
            "ModuleTaskWorkerAgent": _ParallelAgent("ModuleTaskWorkerAgent"),
            "PageTaskWorkerAgent": _ParallelAgent("PageTaskWorkerAgent"),
            "IntegrationTaskWorkerAgent": _ParallelAgent("IntegrationTaskWorkerAgent"),
        },
        context_variables=context,
        structured_output={
            "DecompositionPlan": {
                "tasks": [
                    {
                        "task_id": "module_contract",
                        "kind": "module",
                        "execution_agent": "ModuleTaskWorkerAgent",
                        "task_prompt": "Create the module contract.",
                    },
                    {
                        "task_id": "campaign_page",
                        "kind": "page",
                        "execution_agent": "PageTaskWorkerAgent",
                        "task_prompt": "Create the campaign page.",
                    },
                    {
                        "task_id": "integration_adapter",
                        "kind": "integration",
                        "execution_agent": "IntegrationTaskWorkerAgent",
                        "task_prompt": "Create the integration adapter.",
                    },
                    {
                        "task_id": "analytics_page",
                        "kind": "page",
                        "execution_agent": "PageTaskWorkerAgent",
                        "task_prompt": "Create the analytics page.",
                    },
                ]
            }
        },
        fresh_agents_per_task=False,
    )
    return context, state


@pytest.mark.asyncio
async def test_runtime_task_batch_smoke_executes_multiple_downstream_agents_with_dependencies() -> None:
    context, seen = await _run_runtime_task_batch_smoke_multi_agent_fixture()

    assert context["runtime_smoke_tasks_status"] == "completed"
    assert seen["module_contract"]["agent"] == "ModuleTaskWorkerAgent"
    assert seen["integration_adapter"]["agent"] == "IntegrationTaskWorkerAgent"
    assert seen["campaign_page"]["agent"] == "PageTaskWorkerAgent"
    assert seen["integration_adapter"]["variables"]["dependency_task_outputs"].keys() == {
        "module_contract"
    }
    assert seen["campaign_page"]["variables"]["dependency_task_outputs"].keys() == {
        "module_contract",
        "integration_adapter",
    }
    assert "[TASK BATCH CONTEXT]" in seen["module_contract"]["message"]
    assert '"current_task_id": "module_contract"' in seen["module_contract"]["message"]
    assert '"kind": "module"' in seen["module_contract"]["message"]
    assert context["runtime_smoke_tasks_results"]["_meta"]["completed_tasks"] == [
        "module_contract",
        "integration_adapter",
        "campaign_page",
    ]


@pytest.mark.asyncio
async def test_runtime_task_batch_smoke_runs_parallel_task_chats() -> None:
    context, state = await _run_runtime_task_batch_smoke_parallel_fixture()

    results = context["runtime_smoke_tasks_results"]
    assert context["runtime_smoke_tasks_status"] == "completed"
    assert state.max_active >= 2
    assert set(state.task_ids) == {
        "module_contract",
        "campaign_page",
        "integration_adapter",
        "analytics_page",
    }
    lifecycle_records = [
        results[task_id]["_ag2_task_lifecycle"]
        for task_id in results["_meta"]["completed_tasks"]
    ]
    assert len(lifecycle_records) == 4
    assert all(record["channel_id"] is None for record in lifecycle_records)
    assert all(record["status"] == "completed" for record in lifecycle_records)
    assert all(
        [event["event"] for event in record["events"]] == ["TaskStarted", "TaskCompleted"]
        for record in lifecycle_records
    )
    assert len({record["task_id"] for record in lifecycle_records}) == 4


@pytest.mark.asyncio
async def test_runtime_task_batch_smoke_rejects_worker_identity_drift() -> None:
    config = load_task_batches_config("RuntimeTaskBatchSmoke", workflows_root=WORKFLOWS_ROOT)
    assert config is not None

    class _WrongKindAgent:
        async def ask(self, message, **kwargs):
            variables = _ag2_task_context(kwargs)
            task_id = variables["current_task_id"]
            return SimpleNamespace(
                body=json.dumps(
                    {
                        "agent_message": "Wrong kind.",
                        "task_id": task_id,
                        "kind": "module",
                        "summary": "This intentionally drifts from the page task kind.",
                        "deliverables": ["drift"],
                        "owned_paths": [],
                        "code_files": None,
                    }
                )
            )

    with pytest.raises(RuntimeError, match="emitted kind 'module'; expected 'page'"):
        await execute_task_batches_for_trigger(
            workflow_name="RuntimeTaskBatchSmoke",
            trigger_agent="TaskPlannerAgent",
            batches_config=config,
            agents={"PageTaskWorkerAgent": _WrongKindAgent()},
            context_variables={},
            structured_output={
                "DecompositionPlan": {
                    "tasks": [
                        {
                            "task_id": "campaign_page",
                            "kind": "page",
                            "execution_agent": "PageTaskWorkerAgent",
                            "task_prompt": "Create the campaign page.",
                        }
                    ]
                }
            },
            fresh_agents_per_task=False,
        )
