import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import reviewed_result_page_task as compiler
from mozaiksai.core.workflow.generator_support.page_action_bindings import (
    reachable_page_action_keys,
)

PAGE = "ui/pages/reports.yaml"
CREATE = "/api/modules/reports/create_report"


@pytest.fixture
def page_case(monkeypatch):
    read = {"module_id": "reports", "action_id": "list_reports"}
    edit = {**read, "action_id": "update_report"}
    create = {**read, "action_id": "create_report"}
    fields = [{"name": "title", "type": "string"}]
    table = {"primitive": "DataTable", "config_hint": json.dumps({"columns": ["title", "suggestion"], "selection": "single"})}
    form = {"primitive": "Form", "config_hint": json.dumps({"fields": fields, "data_source": edit})}
    page = {"name": "Reports", "route": "/reports", "ui_surface": "declarative_page", "sections": [table, form]}
    planned = {**page, "sections_hint": [{**table, "data_source": read}, {**form, "data_source": edit}],
               "required_write_actions": [create, edit], "retained_ask_context": []}
    submit = {"id": "add", "label": "Add", "action_type": "submit", "href": CREATE}
    original = {"schema_version": "mozaiks.app_page.v1", "name": "reports", "title": "Reports",
                "page_type": "record_list", "layout": "full-width", "route": "/reports", "sections": [
        {"id": "header", "primitive": "PageHeader", "config": {"title": "Reports", "subtitle": "Reports stay immutable."}},
        {"id": "add-form", "primitive": "Form", "config": {"fields": [{"name": "title", "label": "Title", "type": "text"}],
                                                          "submit_action": submit}},
        {"id": "records", "primitive": "DataTable", "config": {"api_endpoint": "/api/modules/reports/list_reports",
         "data_key": "items", "columns": [{"key": k, "label": k.title()} for k in ("title", "body")]}},
    ]}
    files = {PAGE: yaml.safe_dump(original, sort_keys=False), "app.json": '{ "appName": "Reports" }\n'}
    row = {"task_id": "reports-page", "initial_agent": "AppSchemaAgent", "task_type": "page_bundle",
           "owned_paths": [PAGE, "app.json"]}
    task = {**row, "current_build_task": deepcopy(row)}
    binding = {PAGE: {"writes": [create], "ask_context": []}}
    collection = {"name": "reports", "entity": "Report", "scope": "app", "tenancy": "per_user", "owner_field": "principal",
                  "fields": [{"name": k, "type": "string"} for k in ("report_id", "title", "body", "suggestion", "principal")],
                  "lifecycle": {"write_mode": "module_action", "module_writes": [{"operation": op, "fields": ["title"]}
                                                                                for op in ("create", "update")]}}
    surface = {"surface_id": "reports", "surface_kind": "module", "owner": "app", "owned_mutations": ["create_report", "update_report"]}
    base = {"app_build_plan": {"pages": [planned], "build_tasks": [row]}, "app_task_batch_items": [deepcopy(task)],
            "experience_spec": {"pages": [page]}, "revision_source_artifact_version_id": "parent",
            "build_mode": "revision", "artifact_version_id": "parent", "retained_workflow_page_bindings": binding,
            "data_contract": {"surfaces": [{**surface, "collections": [collection]}]},
            "design_surface_map": {"surfaces": [surface]}, "impact_set": {"affected_bundle_paths": ["ui/pages/*.yaml"]}}
    actions = {op: {"id": op} for op in ("create_report", "update_report", "list_reports")}
    string = {"type": "string"}
    actions["create_report"]["input_schema"] = {"type": "object", "properties": {"title": string}, "required": ["title"]}
    actions["update_report"]["input_schema"] = {"type": "object", "properties": {"report_id": string, "title": string}, "required": ["report_id"]}
    record_schema = {"type": "object", "properties": {k: string for k in ("report_id", "title", "body", "suggestion")}}
    actions["list_reports"]["output_schema"] = {"type": "object", "properties": {"items": {"type": "array", "items": record_schema}}}
    record = SimpleNamespace(id="parent", commit_metadata=SimpleNamespace(metadata={"build_id": "original-build"}))
    lookup = AsyncMock(return_value=(record, files))
    monkeypatch.setattr(compiler, "read_revision_source", lookup)
    monkeypatch.setattr(compiler, "retained_workflow_page_bindings", lambda *_: deepcopy(binding))
    grant = {"module_id": "reports", "result_write": {"source_action": "create_report", "fields": ["suggestion"],
                                                      "collection": "reports", "record_key": "report_id"}}
    monkeypatch.setattr(compiler, "pinned_result_workflows", lambda _: [{"result_write_plans": [grant]}])
    monkeypatch.setattr(compiler, "module_action_index_from_context", lambda _: {"reports": actions})
    return task, base, files, original, lookup


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "different_source", "hidden_field", "alternate_field", "other_column",
                                    "manifest_requested", "new_surface", "changed_baseline"])
async def test_scoped_existing_page_retains_its_controls_and_receives_only_approved_result(page_case, change):
    task, base, files, original, lookup = page_case
    design = base["experience_spec"]["pages"][0]["sections"]
    plan = base["app_build_plan"]["pages"][0]["sections_hint"]
    if change == "different_source":
        design[0]["data_source"] = {"module_id": "private", "action_id": "all"}
    elif change == "hidden_field":
        hint = json.loads(design[1]["config_hint"])
        hint["fields"][0]["hidden"] = True
        design[1]["config_hint"] = plan[1]["config_hint"] = json.dumps(hint)
    elif change == "alternate_field":
        hint = json.loads(design[1]["config_hint"])
        hint["fields"][0]["name"] = "suggestion"
        design[1]["config_hint"] = plan[1]["config_hint"] = json.dumps(hint)
    elif change == "other_column":
        hint = json.loads(design[0]["config_hint"])
        hint["columns"].append("principal")
        design[0]["config_hint"] = plan[0]["config_hint"] = json.dumps(hint)
    elif change == "manifest_requested":
        base["impact_set"]["affected_bundle_paths"].append("app.json")
    elif change == "new_surface":
        task["owned_paths"].append("ui/pages/new.yaml")
    elif change == "changed_baseline":
        base["generated_files"] = {"app.json": "new manifest"}
    offered = await compiler.reviewed_result_page_task_output(task, base, {})
    if change:
        assert offered is None
        return
    assert offered.source_files == files
    assert (offered.page_path, offered.module_id, offered.source_build_id, offered.source_artifact_version_id) == (
        PAGE, "reports", "original-build", "parent",
    )
    lookup.assert_awaited_once_with(base, "parent", require_accepted=True)
    result = {f["filename"]: f["content"] for f in offered.candidate["code_files"]}
    assert set(result) == files.keys() and result["app.json"] == files["app.json"]
    page = yaml.safe_load(result[PAGE])
    assert page["sections"][:2] == original["sections"][:2]
    config = page["sections"][2]["config"]
    assert [v["key"] for v in config["columns"]] == ["title", "body", "suggestion"]
    assert config["selection"] == "single"
    assert reachable_page_action_keys([page], require_selected_row_prefill=True) == {
        "reports/create_report", "reports/update_report", "reports/list_reports",
    }


@pytest.fixture
def visible_copy():
    text = """# preserve formatting
name: reports
sections:
- id: reports-heading
  primitive: PageHeader
  config: {title: Reports, subtitle: Immutable reports are listed here.}
- id: create-report
  primitive: Modal
  config:
    description: Once saved a report cannot change.
- id: actions
  primitive: Form
  config: {submit_action: {href: /api/modules/reports/update_report}}
"""
    files = {PAGE: text, "app.json": '{"appName":"Reports"}'}
    source = compiler.VerifiedResultPageSource({}, files, "v4", "accepted", "reports", PAGE)
    fields = compiler.reviewed_page_copy_fields(source, files)
    edits = [{**fields[0], "replacement": "Create and edit your reports."},
             {**fields[1], "replacement": "Give this report a title. You can edit it later."}]
    return source, files, edits


def test_copy_edits_change_only_the_two_verified_scalar_spans(visible_copy):
    source, files, edits = visible_copy
    assert compiler.apply_reviewed_page_copy(source, files, []) == files
    changed = compiler.apply_reviewed_page_copy(source, files, edits)
    expected = files[PAGE]
    for edit in edits:
        expected = expected.replace(edit["expected"], json.dumps(edit["replacement"]))
    assert changed == {**files, PAGE: expected}
    assert yaml.safe_load(changed[PAGE])["sections"][2]["config"]["submit_action"]["href"] == "/api/modules/reports/update_report"


@pytest.mark.parametrize("fault", ["id", "field", "stale", "new_target", "blank", "long", "multiline", "html",
                                   "duplicate", "count", "unknown_key", "changed_manifest", "ambiguous_id", "alias", "binding"])
def test_copy_edits_cannot_change_behavior_or_other_sections(visible_copy, fault):
    source, files, edits = visible_copy
    issued = deepcopy(files)
    attempt = deepcopy(edits[:1])
    first = attempt[0]
    if fault == "id":
        first["section_id"] = "missing"
    elif fault == "field":
        first["field"] = "submit_action.href"
    elif fault == "stale":
        first["expected"] = "new instruction"
    elif fault == "new_target":
        issued[PAGE] = issued[PAGE].replace(first["expected"], "different proposed text")
    elif fault == "blank":
        first["replacement"] = " "
    elif fault == "long":
        first["replacement"] = "x" * 513
    elif fault == "multiline":
        first["replacement"] = "title" + chr(10) + "sections: []"
    elif fault == "html":
        first["replacement"] = "<span>Reports</span>"
    elif fault == "binding":
        first["replacement"] = "Currently selected: {selected_row.private_field}"
    elif fault == "duplicate":
        attempt.append(dict(first))
    elif fault == "count":
        attempt *= 9
    elif fault == "unknown_key":
        first["path"] = "app.json"
    elif fault == "changed_manifest":
        issued["app.json"] = "{}"
    elif fault == "ambiguous_id":
        issued[PAGE] = issued[PAGE].replace("id: actions", "id: reports-heading")
    elif fault == "alias":
        issued[PAGE] += "other: &ref identity" + chr(10) + "again: *ref" + chr(10)
    with pytest.raises(ValueError):
        compiler.apply_reviewed_page_copy(source, issued, attempt)

def test_existing_interpolated_modal_copy_is_not_offered(visible_copy):
    source, files, _ = visible_copy
    files[PAGE] = files[PAGE].replace("Once saved a report cannot change.", "Record {selected_row.id}.")
    assert [target["field"] for target in compiler.reviewed_page_copy_fields(source, files)] == ["subtitle"]


def test_folded_scalar_keeps_following_section_on_its_original_line(visible_copy):
    source, files, edits = visible_copy
    expected = edits[1]["expected"]
    files[PAGE] = files[PAGE].replace("description: " + expected, "description: >-" + chr(10) + "      " + expected)
    updated = compiler.apply_reviewed_page_copy(source, files, edits[1:])
    assert yaml.safe_load(updated[PAGE])["sections"][1]["config"]["description"] == edits[1]["replacement"]
    assert updated[PAGE].endswith(files[PAGE].split("id: actions")[1])


@pytest.mark.asyncio
async def test_invalid_optional_page_composition_uses_normal_author(page_case, monkeypatch):
    task, base, *_ = page_case
    def unsupported(*_args, **_kwargs):
        raise ValueError("unsupported page")
    monkeypatch.setattr(compiler, "normalize_planned_page_content", unsupported)
    assert await compiler.reviewed_result_page_task_output(task, base, {}) is None
