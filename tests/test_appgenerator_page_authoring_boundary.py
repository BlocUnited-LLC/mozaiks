from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from tests.test_appgenerator_page_data_sources import _module_files, _pages
from tests.test_appgenerator_task_batch_contracts import _base_plan


def _candidate(kind, *, endpoint=False):
    page = _pages()[1]
    if endpoint:
        config = page["sections"][0]["config"]
        config.pop("data_source")
        config["api_endpoint"] = "/api/modules/task_management/list_tasks"
    if kind == "page":
        return {"ui/pages/tasks.yaml": yaml.safe_dump(page)}
    return {"modules/task_management/contracts/admin.yaml": yaml.safe_dump({
        "panels": [{"id": "overview", "sections": page["sections"]}],
    })}


def _output(files):
    return {"code_files": [{"filename": path, "content": content} for path, content in files.items()]}


def test_plan_cannot_assign_declarative_page_to_controller():
    plan = _base_plan()
    plan["build_tasks"][1].update(task_type="api_surface", surface_kind="module", initial_agent="ControllerAgent")
    bridge = ContextVariablesBridge({})
    with pytest.raises(ValueError, match="these require task_type='page_bundle'"):
        app_build_plan(AppBuildPlan=plan, context_variables=bridge)
    assert bridge.get("app_plan_ready") is False


@pytest.mark.parametrize("kind", ["page", "admin"])
def test_raw_page_and_admin_url_candidates_are_rejected_before_bridge_write(kind):
    bridge = ContextVariablesBridge({"generated_files": _module_files()})
    before = bridge.snapshot()
    with pytest.raises(ValueError, match="model-authored endpoint URLs are forbidden"):
        save_generated_code(StructuredOutputOverlay(bridge, _output(_candidate(kind, endpoint=True))))
    assert bridge.snapshot() == before


@pytest.mark.parametrize("kind", ["page", "admin"])
def test_raw_typed_pair_candidates_compile_against_current_and_candidate_inventory(kind):
    # Raw admin output may declare its module in the same candidate. Page repair
    # consumes the admitted inventory; neither path resolves identities from URLs.
    bridge = ContextVariablesBridge({"generated_files": _module_files() if kind == "page" else {}})
    candidate = {**(_module_files() if kind == "admin" else {}), **_candidate(kind)}
    save_generated_code(StructuredOutputOverlay(bridge, _output(candidate)))
    saved = {entry["filename"]: entry["content"] for entry in bridge.get("code_files")}
    path = next(iter(_candidate(kind)))
    assert "api_endpoint: /api/modules/task_management/list_tasks" in saved[path]
    assert "data_source" not in saved[path]


def test_unknown_raw_admin_pair_cannot_reach_saved_files():
    bridge = ContextVariablesBridge({"generated_files": {}})
    with pytest.raises(ValueError, match="unknown module/action"):
        save_generated_code(StructuredOutputOverlay(bridge, _output(_candidate("admin"))))
    assert bridge.get("code_files") is None


def test_raw_admin_cannot_bind_another_modules_action():
    content = next(iter(_candidate("admin").values()))
    bridge = ContextVariablesBridge({"generated_files": _module_files()})
    output = _output({"modules/another_module/contracts/admin.yaml": content})
    with pytest.raises(ValueError, match="unknown module/action 'task_management/list_tasks'"):
        save_generated_code(StructuredOutputOverlay(bridge, output))
    assert bridge.get("code_files") is None


@pytest.mark.parametrize("kind", ["page", "admin"])
def test_pending_module_deletion_excludes_current_and_dependency_actions(kind):
    bridge = ContextVariablesBridge({
        "generated_files": _module_files(), "dependency_task_outputs": {"module_task": _output(_module_files())},
    })
    output = {**_output(_candidate(kind)), "deleted_files": ["./modules/task_management/module.yaml"]}
    before = bridge.snapshot()
    with pytest.raises(ValueError, match="unknown module/action 'task_management/list_tasks'"):
        save_generated_code(StructuredOutputOverlay(bridge, output))
    assert bridge.snapshot() == before


@pytest.mark.parametrize("kind", ["page", "admin"])
def test_identical_admitted_compiled_readback_is_preserved(kind):
    baseline = {**_module_files(), **_candidate(kind, endpoint=True)}
    bridge = ContextVariablesBridge({"generated_files": baseline})
    save_generated_code(StructuredOutputOverlay(bridge, _output(baseline)))
    assert {entry["filename"]: entry["content"] for entry in bridge.get("code_files")} == baseline


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["page", "admin"])
@pytest.mark.parametrize("endpoint", [False, True])
async def test_task_admission_enforces_raw_page_and_admin_sources(monkeypatch, kind, endpoint):
    candidate = _candidate(kind, endpoint=endpoint)
    task = {
        "task_id": "authoring_task", "task_type": "api_surface" if kind == "page" else "module_contract",
        "capability_pack_id": "task_management", "initial_agent": "ControllerAgent" if kind == "page" else "ConfigMiddlewareAgent",
        "initial_message": "Write the owned artifact.", "owned_paths": list(candidate), "depends_on": [],
    }
    bridge = ContextVariablesBridge({
        "generated_files": _module_files(), "app_build_plan": {"build_tasks": [task]}, "app_task_batch_items": [task],
    })

    async def run(_runner, request):
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=_output(candidate))

    async def checkpoint(updates):
        for key, value in updates.items():
            bridge.set(key, value)

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    config = task_batches.load_task_batches_config(
        "AppGenerator", workflows_root=Path(__file__).resolve().parents[1] / "factory_app" / "workflows",
    )
    snapshot = bridge.snapshot()
    await task_batches.execute_task_batches_for_trigger(
        workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=config,
        agents={task["initial_agent"]: object()}, context_variables=snapshot,
        chat_id="page-authoring", app_id="page-authoring", user_id="user-1", fresh_agents_per_task=False,
        parent_channel_id="page-authoring-parent", checkpoint=checkpoint,
    )
    results = snapshot["app_task_batch_results"]
    if endpoint:
        assert "model-authored endpoint URLs are forbidden" in results["_failed"]["authoring_task"]["error"]
        assert "authoring_task" not in results
    else:
        assert snapshot["app_task_batch_status"] == "completed", results
        assert "api_endpoint: /api/modules/task_management/list_tasks" in results["authoring_task"]["code_files"][0]["content"]
