"""Generated statusbars use the runtime contract throughout bundle assembly."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZipFile

import pytest
import yaml

from factory_app.workflows.AgentGenerator.tools.statusbar_materialization import (
    materialize_workflow_statusbar,
)
from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
    validate_workflow_bundle_structure,
)
from mozaiksai.core.workflow.declarative.contracts import MiddlewareConfig
from mozaiksai.core.workflow.outputs.structured import build_models_from_config
from tests.test_agentgenerator_generate_and_download_collection import (
    _Context,
    _minimal_workflow_files,
    generate_and_download_module,
)

WORKFLOWS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
STATUSBAR = [{"agent": "PlannerAgent", "variables": ["phase"]}]


def _file(entry, filename):
    return next(file for file in entry["files"] if file["filename"] == filename)


def _entry():
    entry = {
        "workflow_name": "StatusWorkflow", "agent_message": "Workflow generated.",
        "pattern_id": 1, "pattern_name": "Pipeline", "outcome_plans": [],
        "statusbar_middleware": deepcopy(STATUSBAR),
        "files": _minimal_workflow_files("StatusWorkflow"),
    }
    context_file = _file(entry, "context_variables.yaml")
    context = yaml.safe_load(context_file["content"])
    context["definitions"]["phase"] = {
        "type": "string", "authority_class": "model_visible_information",
        "source": {"type": "state", "default": "planning"},
    }
    context["agents"]["PlannerAgent"]["variables"].append("phase")
    context_file["content"] = yaml.safe_dump(context)
    for file in entry["files"]:
        file["installRequirements"] = []
    return entry


def _task_batch_entry(source_type, visibility=None):
    entry = _entry()
    entry["files"] = _minimal_workflow_files("StatusWorkflow", include_task_batches=True)
    keys = ["review_tasks_results", "review_tasks_status"]
    entry["statusbar_middleware"] = [{"agent": "PlannerAgent", "variables": keys}]
    context_file = _file(entry, "context_variables.yaml")
    context = yaml.safe_load(context_file["content"])
    for key in keys:
        context["definitions"][key] = {
            "type": "object", "source": {"type": source_type, "default": {}},
            **(visibility or {}),
        }
    context["agents"]["PlannerAgent"]["variables"].extend(keys)
    context_file["content"] = yaml.safe_dump(context)
    for file in entry["files"]:
        file["installRequirements"] = []
    return entry


@pytest.fixture(scope="module")
def bundle_model():
    config = yaml.safe_load((WORKFLOWS / "AgentGenerator" / "structured_outputs.yaml").read_text(encoding="utf-8"))
    return build_models_from_config(config["models"], exact_model_ids=frozenset(config["models"]))[
        config["registry"]["WorkflowBundleBuilderAgent"]
    ]


def test_statusbar_materialization_preserves_prompt_middleware_and_is_idempotent():
    entry = _entry()
    prompts = [{"agent": "PlannerAgent", "filename": "tools/context.py", "function": "enrich"}]
    _file(entry, "middleware.yaml")["content"] = yaml.safe_dump({"prompt_middleware": prompts})
    original = deepcopy(entry)

    emitted = materialize_workflow_statusbar(entry)

    assert entry == original
    assert emitted == materialize_workflow_statusbar(emitted)
    payload = yaml.safe_load(_file(emitted, "middleware.yaml")["content"])
    assert payload == {"prompt_middleware": prompts, "statusbar_middleware": STATUSBAR}
    assert [file for file in emitted["files"] if file["filename"] != "middleware.yaml"] == [
        file for file in original["files"] if file["filename"] != "middleware.yaml"
    ]


@pytest.mark.parametrize("raw_statusbar", [None, STATUSBAR])
def test_missing_typed_field_preserves_existing_middleware(raw_statusbar):
    entry = _entry()
    del entry["statusbar_middleware"]
    if raw_statusbar is not None:
        _file(entry, "middleware.yaml")["content"] = yaml.safe_dump({"statusbar_middleware": raw_statusbar})
    original = deepcopy(entry)
    assert materialize_workflow_statusbar(entry) is entry
    assert entry == original
    report = validate_workflow_bundle_structure(bundle_entries=[entry])
    assert report["valid"], report["errors"]


def test_explicit_empty_statusbar_is_emitted():
    entry = _entry()
    entry["statusbar_middleware"] = []
    emitted = materialize_workflow_statusbar(entry)
    assert yaml.safe_load(_file(emitted, "middleware.yaml")["content"]) == {
        "prompt_middleware": [], "statusbar_middleware": [],
    }


@pytest.mark.parametrize("raw_statusbar", [[], [{"agent": "WorkerAgent", "variables": ["phase"]}]])
def test_conflicting_raw_statusbar_fails_assembly(raw_statusbar):
    entry = _entry()
    _file(entry, "middleware.yaml")["content"] = yaml.safe_dump({"statusbar_middleware": raw_statusbar})
    with pytest.raises(ValueError, match="conflicts"):
        materialize_workflow_statusbar(entry)
    report = validate_workflow_bundle_structure(bundle_entries=[entry])
    assert not report["valid"]
    assert any("statusbar materialization failed" in error for error in report["errors"])


@pytest.mark.parametrize("declaration", [
    None,
    {"agent": "PlannerAgent", "variables": ["phase"]},
    [{"agent": "PlannerAgent", "variables": []}],
    [{"agent": "PlannerAgent", "variables": ["phase", "phase"]}],
    STATUSBAR * 2,
    [{"agent": "PlannerAgent", "variables": ["phase"], "callback": "tools/context.py"}],
])
def test_invalid_statusbar_shape_fails_shared_runtime_schema(declaration):
    entry = _entry()
    entry["statusbar_middleware"] = declaration
    with pytest.raises(ValueError):
        materialize_workflow_statusbar(entry)
    report = validate_workflow_bundle_structure(bundle_entries=[entry])
    assert not report["valid"]
    assert any("statusbar materialization failed" in error for error in report["errors"])


@pytest.mark.parametrize("defect", ["unknown_agent", "unknown_variable", "unexposed", "hidden", "tool_only", "remote"])
def test_statusbar_quality_gate_closes_agent_context_and_visibility_references(defect):
    entry = _entry()
    context_file = _file(entry, "context_variables.yaml")
    context = yaml.safe_load(context_file["content"])
    if defect == "unknown_agent":
        entry["statusbar_middleware"][0]["agent"] = "MissingAgent"
    elif defect == "unknown_variable":
        entry["statusbar_middleware"][0]["variables"] = ["missing"]
    elif defect == "unexposed":
        context["agents"]["PlannerAgent"]["variables"].remove("phase")
    elif defect == "hidden":
        context["definitions"]["phase"]["model_visible"] = False
    elif defect == "tool_only":
        context["definitions"]["phase"]["authority_class"] = "tool_only_information"
    else:
        entry["files"].append({"filename": "a2a.yaml", "content": yaml.safe_dump({"agents": [{
            "name": "PlannerAgent", "url": "https://remote.example/agent",
        }]})})
    context_file["content"] = yaml.safe_dump(context)
    report = validate_workflow_bundle_structure(bundle_entries=[entry])
    assert not report["valid"]
    assert any("middleware.yaml statusbar contract mismatch" in error for error in report["errors"])


@pytest.mark.parametrize("source_type", ["config", "file", "data_reference"])
@pytest.mark.parametrize("visibility", [{"model_visible": False}, {"authority_class": "tool_only_information"}])
def test_task_batch_statusbar_respects_explicit_hidden_context(source_type, visibility):
    entry = _task_batch_entry(source_type, visibility)
    report = validate_workflow_bundle_structure(bundle_entries=[entry])
    assert not report["valid"]
    assert any("not model-visible" in error for error in report["errors"])


@pytest.mark.parametrize("source_type", [None, "config", "file", "data_reference"])
def test_typed_statusbar_survives_download_zip_and_runtime_loading(bundle_model, monkeypatch, tmp_path, source_type):
    entry = _task_batch_entry(source_type) if source_type else _entry()
    candidate = bundle_model.model_validate(entry).model_dump(mode="json")
    assert candidate["statusbar_middleware"] == entry["statusbar_middleware"]
    original = deepcopy(candidate)
    context = _Context({
        "chat_id": "chat-status", "app_id": "app-status", "user_id": "user-status",
        "workflow_name": "AgentGenerator", "pack_name": "StatusWorkflow", "artifact_version_id": "av_status",
        "is_multi_workflow": False, "workflow_bundle_results": {"status": candidate},
    })
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    monkeypatch.setattr(generate_and_download_module, "record_workflow_export", AsyncMock())
    monkeypatch.setattr(generate_and_download_module, "record_workflow_artifacts", AsyncMock())
    monkeypatch.setattr(generate_and_download_module, "resolve_agent_api_url", lambda app_id: "https://api.test")
    monkeypatch.setattr(generate_and_download_module, "resolve_agent_websocket_url", lambda app_id: "wss://ws.test")
    monkeypatch.setattr(generate_and_download_module, "_register_workflow_bundle_artifact_version", AsyncMock(
        return_value=SimpleNamespace(id="av_status"),
    ))
    monkeypatch.setattr(generate_and_download_module, "use_ui_tool", AsyncMock(return_value={
        "status": "completed", "data": {}, "agentContext": {},
    }))

    result = asyncio.run(generate_and_download_module.generate_and_download(
        DownloadRequest={"confirmation_only": False, "storage_backend": "none"},
        agent_message="Done.", context_variables=context,
    ))

    assert result["status"] == "success", result
    assert candidate == original
    assert context.get("workflow_bundle_validation_status") == "passed"
    emitted = context.get("workflow_bundle_results")["status"]
    middleware = yaml.safe_load(_file(emitted, "middleware.yaml")["content"])
    assert MiddlewareConfig.model_validate(middleware).statusbar_middleware[0].model_dump() == entry["statusbar_middleware"][0]
    zip_path = Path(result["ui_files"][0]["path"])
    with ZipFile(zip_path) as archive:
        middleware_path = next(name for name in archive.namelist() if name.endswith("/middleware.yaml"))
        assert yaml.safe_load(archive.read(middleware_path)) == middleware

    from mozaiksai.core.workflow.workflow_manager import UnifiedWorkflowManager

    monkeypatch.setattr(UnifiedWorkflowManager, "_instance", None)
    manager = UnifiedWorkflowManager(workflows_base_path=str(zip_path.parent))
    info = manager.get_workflow_info("StatusWorkflow")
    assert info["status"] == "loaded", info["error"]
