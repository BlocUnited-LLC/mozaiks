from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from ag2.events import ModelMessage

from factory_app.workflows.AgentGenerator.tools.hook_primitive_catalog import (
    inject_primitive_catalog,
)
from factory_app.workflows.AgentGenerator.tools.statusbar_materialization import (
    materialize_workflow_statusbar,
)
from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
    load_bundle_entries_from_root,
    validate_workflow_bundle_structure,
)
from mozaiksai.core.workflow.declarative.contracts import (
    ImageGenerationSpec,
    WorkflowProgressConfig,
)
from mozaiksai.core.workflow.execution.middleware import MozaiksPromptMiddleware
from mozaiksai.core.workflow.workflow_manager import UnifiedWorkflowManager
from tests.test_smoke_agentgenerator_live_pack import _write_valid_workflow

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_bundle_builder_receives_the_runtime_progress_schema_without_drift():
    context = SimpleNamespace(variables={})
    middleware = MozaiksPromptMiddleware(
        ModelMessage("Generate a workflow."), context,
        middleware_functions=[inject_primitive_catalog, inject_primitive_catalog],
        agent_name="WorkflowBundleBuilderAgent", base_system_message="Generate a workflow.",
        context_bridge={},
    )
    first = await middleware._run_prompt_middleware(context)
    for header, model in (
        ("WORKFLOW PROGRESS CONTRACT", WorkflowProgressConfig),
        ("GENERATED MEDIA REVIEW CONTRACT", ImageGenerationSpec),
    ):
        assert first.count(f"[{header}]") == 1
        section = first.split(f"[{header}]\n", 1)[1].split("\n\n[", 1)[0]
        assert json.loads(section.split("\n", 1)[1]) == model.model_json_schema()
    assert await middleware._run_prompt_middleware(context) == first


@pytest.mark.parametrize("stage_id,valid", [("reading", True), ("undeclared", False)])
@pytest.mark.parametrize("import_line,report", [
    ("from mozaiksai.core.workflow.ui_tools import emit_tool_progress_event as report", "report"),
    ("from mozaiksai.core.workflow import ui_tools", "ui_tools.emit_tool_progress_event"),
    ("import mozaiksai.core.workflow.ui_tools as ui", "ui.emit_tool_progress_event"),
    ("import mozaiksai.core.workflow.ui_tools", "mozaiksai.core.workflow.ui_tools.emit_tool_progress_event"),
])
def test_generated_progress_references_are_checked_before_packaging(tmp_path, stage_id, valid, import_line, report):
    workflow = tmp_path / "ProgressReview"
    _write_valid_workflow(workflow, workflow.name)
    (workflow / "ui_config.yaml").write_text(yaml.safe_dump({
        "visual_agents": ["PlannerAgent"],
        "progress": {"stages": [{"id": "reading", "label": "Reading items", "unit": "items"}]},
    }), encoding="utf-8")
    (workflow / "tools").mkdir(exist_ok=True)
    (workflow / "tools" / "read.py").write_text(
        f"{import_line}\n"
        "async def read(context_variables):\n"
        f"    await {report}(tool_name='read', stage_id={stage_id!r}, context_variables=context_variables)\n"
        "    return {'count': 0}\n",
        encoding="utf-8",
    )
    (workflow / "tools.yaml").write_text(yaml.safe_dump({
        "tools": [{"agent": "PlannerAgent", "file": "tools/read.py", "function": "read",
                   "description": "Read items", "tool_type": "Agent_Tool", "auto_tool_call": False}],
        "lifecycle_tools": [],
    }), encoding="utf-8")
    expected = [{"name": workflow.name}]
    result = validate_workflow_bundle_structure(
        bundle_entries=load_bundle_entries_from_root(tmp_path, expected), expected_workflows=expected,
    )
    assert result["valid"] is valid, result["errors"]
    if not valid:
        assert any("progress stage 'undeclared'" in error for error in result["errors"])


@pytest.mark.parametrize("workflow", ["AppGenerator", "AgentGenerator", "ExistingAppDiscovery"])
def test_factory_progress_declarations_load_through_runtime(workflow, monkeypatch):
    monkeypatch.setattr(UnifiedWorkflowManager, "_instance", None)
    manager = UnifiedWorkflowManager(workflows_base_path=str(ROOT / "factory_app/workflows"))
    config = manager.get_config(workflow)
    assert config and config["progress"]["stages"]


@pytest.mark.parametrize("defect", [None, "unknown_progress_stage", "hidden_statusbar_variable"])
def test_generated_statusbar_and_progress_validate_together(tmp_path, monkeypatch, defect):
    workflow = tmp_path / "ProgressStatusReview"
    _write_valid_workflow(workflow, workflow.name, include_task_batches=True, visual_agents=["PlannerAgent"])
    progress = {"stages": [{"id": "reviewing", "label": "Reviewing items", "unit": "items"}]}
    (workflow / "ui_config.yaml").write_text(yaml.safe_dump({
        "visual_agents": ["PlannerAgent"], "progress": progress,
    }), encoding="utf-8")
    batch_file = workflow / "extended_orchestration" / "task_batches.yaml"
    batches = yaml.safe_load(batch_file.read_text(encoding="utf-8"))
    batches["conveyors"][0]["progress_stage"] = "undeclared" if defect == "unknown_progress_stage" else "reviewing"
    batch_file.write_text(yaml.safe_dump(batches), encoding="utf-8")
    if defect == "hidden_statusbar_variable":
        context_file = workflow / "context_variables.yaml"
        context = yaml.safe_load(context_file.read_text(encoding="utf-8"))
        context["definitions"]["triage_tasks_status"]["model_visible"] = False
        context_file.write_text(yaml.safe_dump(context), encoding="utf-8")
    entries = load_bundle_entries_from_root(tmp_path, [{"name": workflow.name}])
    statusbar = [{"agent": "PlannerAgent", "variables": ["triage_tasks_status"]}]
    entries[0]["statusbar_middleware"] = statusbar

    result = validate_workflow_bundle_structure(bundle_entries=entries)

    if defect:
        expected = "undeclared progress stage" if defect == "unknown_progress_stage" else "not model-visible"
        assert not result["valid"]
        assert any(expected in error for error in result["errors"]), result["errors"]
    else:
        assert result["valid"], result["errors"]
        materialized = materialize_workflow_statusbar(entries[0])
        middleware = next(file["content"] for file in materialized["files"] if file["filename"] == "middleware.yaml")
        (workflow / "middleware.yaml").write_text(middleware, encoding="utf-8")
        monkeypatch.setattr(UnifiedWorkflowManager, "_instance", None)
        manager = UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
        config = manager.get_config(workflow.name)
        assert config["progress"] == progress
        assert config["middleware"]["statusbar_middleware"] == statusbar
