from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from mozaiksai.core.workflow.task_batches import load_task_batches_config


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = ROOT / "factory_app" / "workflows"
WORKFLOW_DIR = WORKFLOWS_ROOT / "RuntimeTaskBatchSmoke"


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_task_batch_smoke_orchestrator_contract() -> None:
    data = _read_yaml(WORKFLOW_DIR / "orchestrator.yaml")

    assert data["workflow_name"] == "RuntimeTaskBatchSmoke"
    assert data["human_in_the_loop"] is False
    assert data["initial_agent"] == "TaskPlannerAgent"
    assert data["orchestration_pattern"] == "ag2_network"


def test_runtime_task_batch_smoke_structured_outputs_contract() -> None:
    data = _read_yaml(WORKFLOW_DIR / "structured_outputs.yaml")

    assert data["registry"]["TaskPlannerAgent"] == "RuntimeTaskBatchPlanOutput"
    assert data["registry"]["TaskWorkerAgent"] == "RuntimeTaskBatchWorkerOutput"
    assert data["registry"]["SynthesisAgent"] == "RuntimeTaskBatchSmokeResult"
    models = data["models"]
    assert "RuntimeTaskBatchPlan" in models
    assert "RuntimeTaskBatchWorkUnit" in models
    assert "RuntimeTaskBatchWorkerOutput" in models
    assert "RuntimeTaskBatchSmokeResult" in models


def test_runtime_task_batch_smoke_declares_task_batches_yaml() -> None:
    config = load_task_batches_config("RuntimeTaskBatchSmoke", workflows_root=WORKFLOWS_ROOT)

    assert config is not None
    batch = config.batches[0]
    assert batch.id == "runtime_smoke_tasks"
    assert batch.trigger_agent == "TaskPlannerAgent"
    assert batch.source.kind == "structured_output"
    assert batch.source.path == "RuntimeTaskBatchPlan.work_units"
    assert batch.worker.agent_field == "initial_agent"
    assert batch.worker.prompt_field == "initial_message"
    assert batch.result.context_key == "task_batch_results"
    assert batch.result.status_key == "task_batch_status"


def test_runtime_task_batch_smoke_handoffs_terminate_after_synthesis() -> None:
    data = _read_yaml(WORKFLOW_DIR / "handoffs.yaml")
    rules = {r["source_agent"]: r["target_agent"] for r in data["handoff_rules"]}

    assert rules["TaskPlannerAgent"] == "SynthesisAgent"
    assert rules["SynthesisAgent"] == "terminate"


def test_runtime_task_batch_synthesis_copies_executor_meta_exactly() -> None:
    data = _read_yaml(WORKFLOW_DIR / "agents.yaml")
    synthesis = next(agent for agent in data["agents"] if agent["name"] == "SynthesisAgent")
    instructions = "\n".join(
        str(section.get("content") or "")
        for section in synthesis["prompt_sections"]
    )

    assert "work_unit_count` MUST equal `task_batch_results._meta.task_count" in instructions
    assert "executed_task_ids` MUST equal `task_batch_results._meta.completed_tasks" in instructions
    assert "result_context_key` MUST equal `task_batch_results._meta.result_context_key" in instructions
    assert "Do not invent task ids" in instructions


def test_runtime_task_batch_synthesis_hook_injects_exact_executor_summary() -> None:
    hooks = _read_yaml(WORKFLOW_DIR / "hooks.yaml")
    assert hooks["hooks"][0]["function"] == "inject_task_batch_synthesis_context"

    module = _load_module(
        WORKFLOW_DIR / "tools" / "hook_task_batch_synthesis.py",
        "tests.runtime_task_batch_synthesis_hook",
    )

    class _Context:
        def __init__(self) -> None:
            self.data = {
                "task_batch_status": "completed",
                "task_batch_results": {
                    "profiles": {"kind": "module", "summary": "Profiles done."},
                    "feed": {"kind": "service", "summary": "Feed done."},
                    "_meta": {
                        "status": "completed",
                        "task_count": 2,
                        "concurrency": 4,
                        "completed_tasks": ["profiles", "feed"],
                        "failed_tasks": [],
                        "result_context_key": "task_batch_results",
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
    assert '"result_context_key": "task_batch_results"' in agent.system_message
