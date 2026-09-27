"""Typed page data sources close the exact live AppGenerator wiring failures."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema
from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _workflow_tool_invocation
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.context.schema import load_context_variables_config
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    module_action_index_from_context,
)
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from mozaiksai.core.workflow.task_batches import _normalize_owned_page_files_from_plan
from tests.factory_context import factory_context

ROOT = Path(__file__).resolve().parents[1]


def _module_files():
    return {"modules/task_management/module.yaml": yaml.safe_dump({
        "schema_version": "mozaiks.module.v1", "module": {"id": "task_management"},
        "actions": [{"id": action} for action in (
            "create_task", "update_task", "delete_task", "list_tasks", "get_dashboard_insights",
        )],
    })}


def _pages():
    source = {"module_id": "task_management", "action_id": "list_tasks"}
    return [{
        "schema_version": "mozaiks.app_page.v1", "name": "dashboard", "title": "Dashboard",
        "route": "/dashboard", "page_type": "analytics_dashboard", "layout": "grid",
        "sections": [
            {"id": "kpi-metrics", "primitive": "SummaryStrip", "config": {
                "data_source": {"module_id": "task_management", "action_id": "get_dashboard_insights"},
                "items": [{"label": "Total tasks", "value_key": "total_tasks"}],
            }},
            {"id": "task-list-overview", "primitive": "DataTable", "config": {
                "columns": [{"key": "title", "label": "Task"}], "data_source": source, "data_key": "items",
            }},
        ],
    }, {
        "schema_version": "mozaiks.app_page.v1", "name": "tasks", "title": "Tasks",
        "route": "/tasks", "page_type": "record_list", "layout": "full-width",
        "sections": [{"id": "task-data-table", "primitive": "DataTable", "config": {
            "columns": [{"key": "title", "label": "Task"}], "data_source": source, "data_key": "items",
        }}],
    }]


def _bridge(initial=None):
    config = load_context_variables_config(yaml.safe_load(
        (ROOT / "factory_app/workflows/AppGenerator/context_variables.yaml").read_text(encoding="utf-8"),
    ))
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=config.definitions)
    bridge = ContextVariablesBridge(factory_context(initial), authority_policy=policy)
    bridge._bind_run(("AppGenerator", "page-data-app", "6ebcbc4b-5422-4953-a1bb-cb4ada3db176"), policy)
    return bridge


def test_live_sections_compile_with_real_context_and_pass_unchanged_wiring_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    context = _bridge({"generated_files": _module_files()})
    with _workflow_tool_invocation(context):
        save_app_schema(
            manifest={"app_name": "Task tracker", "pages": ["dashboard", "tasks"], "default_route": "/dashboard"},
            pages=_pages(), context_variables=context,
        )
    report = asyncio.run(validate_wiring(context))
    assert report["passed"] is True, report["failed_tests"]
    assert {(item["page"], item["section"], item["endpoint"]) for item in report["wired"]} == {
        ("dashboard", "kpi-metrics", "/api/modules/task_management/get_dashboard_insights"),
        ("dashboard", "task-list-overview", "/api/modules/task_management/list_tasks"),
        ("tasks", "task-data-table", "/api/modules/task_management/list_tasks"),
    }
    assert not report["orphaned_pages"]
    assert "data_source" not in context.get("generated_files")["ui/pages/tasks.yaml"]


def test_original_live_url_evidence_still_fails_wiring_gate():
    pages = _pages()
    for page in pages:
        for section in page["sections"]:
            section["config"].pop("data_source")
            section["config"]["api_endpoint"] = "/api/modules/dashboard" if section["id"] == "kpi-metrics" else "/api/tasks"
    files = _module_files()
    files.update({f"ui/pages/{page['name']}.yaml": yaml.safe_dump(page) for page in pages})
    report = asyncio.run(validate_wiring(_bridge({"generated_files": files})))
    assert report["passed"] is False
    assert {(item["page"], item["section"]) for item in report["orphaned_pages"]} == {
        ("dashboard", "task-list-overview"), ("tasks", "task-data-table"),
    }
    assert any("kpi-metrics" in str(item) for item in report["failed_tests"])


@pytest.mark.parametrize("source", [
    {"module_id": "dashboard", "action_id": "get_dashboard_insights"},
    {"module_id": "task_management", "action_id": "list_task"},
])
def test_unknown_pair_fails_before_any_write_with_real_context(source, tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    context = _bridge({"generated_files": _module_files()})
    pages = _pages()
    pages[0]["sections"][0]["config"]["data_source"] = source
    with _workflow_tool_invocation(context), pytest.raises(ValueError, match="unknown module/action"):
        save_app_schema(manifest={"app_name": "Task tracker"}, pages=pages, context_variables=context)
    assert not list(tmp_path.rglob("*"))


def test_deleted_module_is_removed_from_closed_inventory_before_page_write(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    files = _module_files()
    path, content = next(iter(files.items()))
    context = _bridge({
        "generated_files": files,
        "dependency_task_outputs": {"prior-module": {"code_files": [{"filename": path, "content": content}]}},
        "deleted_files": ["./modules/task_management/module.yaml"],
    })
    assert module_action_index_from_context(context) == {}
    with _workflow_tool_invocation(context), pytest.raises(ValueError, match="unknown module/action"):
        save_app_schema(manifest={"app_name": "Task tracker"}, pages=_pages(), context_variables=context)
    assert not list(tmp_path.rglob("*"))
    assert context.get("generated_files")[path] == content


@pytest.mark.parametrize("api_surface", ["internal", "admin_internal"])
def test_internal_actions_cannot_be_page_or_admin_data_sources(api_surface, tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    path = "modules/task_management/module.yaml"
    manifest = yaml.safe_load(_module_files()[path])
    next(action for action in manifest["actions"] if action["id"] == "list_tasks")["api_surface"] = api_surface
    context = _bridge({"generated_files": {path: yaml.safe_dump(manifest)}})
    assert "list_tasks" not in module_action_index_from_context(context)["task_management"]
    with _workflow_tool_invocation(context), pytest.raises(ValueError, match="unknown module/action"):
        save_app_schema(manifest={"app_name": "Tasks"}, pages=[_pages()[1]], context_variables=context)
    with pytest.raises(ValueError, match="unknown module/action"):
        extract_code_file_map_from_payload({"module_contract": {
            "module_id": "task_management", "module_yaml": manifest,
            "admin_yaml": {"panels": [{"id": "tasks", "sections": _pages()[1]["sections"]}]},
        }})
    assert not list(tmp_path.rglob("*"))


def test_task_worker_compiles_against_dependency_module_contract():
    dependency = {"module_contract": {"module_id": "task_management", "module_yaml": yaml.safe_load(
        _module_files()["modules/task_management/module.yaml"],
    )}}
    context = _bridge({"dependency_task_outputs": {"module-task": dependency}})
    assert module_action_index_from_context(context)["task_management"] == {
        "create_task", "update_task", "delete_task", "list_tasks", "get_dashboard_insights",
    }
    # The runner snapshots/detaches the bridge for a worker; it supplies only
    # declared dependency outputs, not all parallel worker history.
    from mozaiksai.core.workflow.context.frozen import detach
    snapshot = {"dependency_task_outputs": detach(context.get("dependency_task_outputs")),
                "app_build_plan": {"pages": [{"name": "tasks", "route": "/tasks"}]}}
    files = _normalize_owned_page_files_from_plan(
        [{"filename": "ui/pages/tasks.yaml", "content": yaml.safe_dump(_pages()[1])}],
        task={"owned_paths": ["ui/pages/tasks.yaml"]}, base_context=snapshot,
    )
    assert "api_endpoint: /api/modules/task_management/list_tasks" in files[0]["content"]
    assert "data_source" not in files[0]["content"]


def test_structured_output_requires_typed_sources_and_never_endpoint_strings():
    models, _ = load_workflow_structured_outputs("AppGenerator")
    for name, payload in (
        ("AppDataTableConfig", {"columns": [{"key": "title"}]}),
        ("AppResourceTableConfig", {"columns": [{"key": "title"}]}),
        ("AppSummaryStripConfig", {"items": []}),
        ("AppMetricConfig", {"label": "Tasks", "value": 1}),
    ):
        fields = models[name].model_json_schema()["properties"]
        assert "data_source" in fields and "api_endpoint" not in fields
        with pytest.raises(ValidationError):
            models[name].model_validate({**payload, "api_endpoint": "/api/tasks"})
    for name, action_type in (("AppSubmitAction", "submit"), ("AppDeleteAction", "delete")):
        model = models[name]
        assert "href" not in model.model_json_schema()["properties"]
        value = model.model_validate({"action_type": action_type, "label": "Save", "data_source": {
            "module_id": "task_management", "action_id": "create_task",
        }})
        assert value.data_source.module_id == "task_management"
        with pytest.raises(ValidationError):
            model.model_validate({"action_type": action_type, "label": "Save", "href": "/api/tasks"})
    hint = models["PrimitiveSectionHint"].model_json_schema()["properties"]
    assert "data_source" in hint


def test_authoring_source_is_not_mutated_by_materialization(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    pages = _pages()
    expected = deepcopy(pages)
    context = _bridge({"generated_files": _module_files()})
    with _workflow_tool_invocation(context):
        save_app_schema(manifest={"app_name": "Tasks", "default_route": "/dashboard"}, pages=pages, context_variables=context)
    assert pages == expected
