from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from ag2.network.policies import CHANNEL_STATE_DEP
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.app_plan_review import validate_plan_coverage
from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema
from mozaiksai.core.runtime.app.provenance import (
    build_default_app_provenance,
    dump_app_provenance_yaml,
)
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.outputs.structured import (
    build_models_from_config,
    get_provider_response_model,
)
from mozaiksai.core.workflow.task_batches import (
    _validate_task_output_ownership,
    execute_task_batches_for_trigger,
    parse_task_batches_config,
)
from scripts import smoke_appgenerator_live_acceptance as acceptance_smoke

ROOT = Path(__file__).resolve().parents[1]


def _page(name="customers"):
    return {
        "schema_version": "mozaiks.app_page.v1", "name": name,
        "route": f"/{name}", "title": name.title(), "page_type": "settings",
        "layout": "full-width", "roles": None, "navigation": None,
        "sections": [{"id": "header", "primitive": "PageHeader", "config": {"title": name.title()}}],
    }


def _output(*pages, manifest=None):
    return {
        "agent_message": "Updated owned pages.", "manifest": manifest, "pages": list(pages),
        "custom_route_bundle": None, "theme_config_patch": None, "shell_config": None,
        "asset_manifest": None, "data_contract": None,
    }


def _manifest():
    return {
        "app_name": "Contact Desk", "version": "1.0.0", "description": None,
        "tagline": None, "value_proposition": None, "auth_strategy": "public",
        "roles": None, "default_route": "/customers", "pages": ["customers"],
        "custom_routes": None,
    }


def _task(name="customers", *, owns_manifest=False):
    return {
        "task_id": name, "task_type": "page_bundle", "initial_agent": "WorkerAgent",
        "initial_message": name, "owned_paths": [f"ui/pages/{name}.yaml"] + (["app.json"] if owns_manifest else []),
    }


def _batch_config():
    return parse_task_batches_config({"version": 1, "batches": [{
        "id": "app_build_tasks", "trigger_agent": "AppPlanAgent",
        "source": {"kind": "context_variable", "path": "app_task_batch_items", "task_model": "AppBuildTask"},
        "result": {"context_key": "app_task_batch_results", "status_key": "app_task_batch_status", "require_owned_paths": True},
    }]})


@pytest.fixture(scope="module")
def schema_model():
    config = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))
    return build_models_from_config(config["models"])["AppSchemaOutput"]


@pytest.mark.parametrize("manifest", [None, _manifest()])
def test_schema_accepts_only_typed_nullable_manifest(schema_model, manifest):
    output = schema_model.model_validate(_output(manifest=manifest)).model_dump(mode="json")
    assert output["manifest"] == manifest
    schema = get_provider_response_model(schema_model).model_json_schema()
    assert "manifest" in schema["required"]
    assert {item["type"] for item in schema["properties"]["manifest"]["anyOf"]} == {"object", "null"}


@pytest.mark.parametrize("invalid", ["string", "list"])
def test_schema_rejects_untyped_manifest(schema_model, invalid):
    output = _output()
    output["manifest"] = "preserve existing" if invalid == "string" else []
    with pytest.raises(ValidationError):
        schema_model.model_validate(output)


def test_provider_requires_explicit_manifest_while_local_model_normalizes_optional(schema_model):
    output = _output()
    output.pop("manifest")
    assert schema_model.model_validate(output).model_dump(mode="json")["manifest"] is None
    with pytest.raises(ValidationError, match="manifest"):
        get_provider_response_model(schema_model).model_validate(output)


def test_null_manifest_materializes_only_owned_pages_without_mutating_payload():
    payload = _output(_page())
    before = json.dumps(payload, sort_keys=True)
    files = extract_code_file_map_from_payload(payload)
    assert set(files) == {"ui/pages/customers.yaml"}
    assert yaml.safe_load(files["ui/pages/customers.yaml"]) == _page()
    assert json.dumps(payload, sort_keys=True) == before


@pytest.mark.parametrize("manifest", [False, "preserve existing", []])
def test_materializer_rejects_untyped_manifest_instead_of_dropping_pages(manifest):
    with pytest.raises(ValueError, match="manifest must be an object or null"):
        extract_code_file_map_from_payload(_output(_page(), manifest=manifest))


@pytest.mark.parametrize("owns_manifest,manifest,error", [
    (False, None, None),
    (True, _manifest(), None),
    (False, _manifest(), "outside owned_paths.*app.json"),
    (True, None, "did not emit required owned_paths.*app.json"),
])
def test_manifest_scope_uses_existing_ownership_gate(owns_manifest, manifest, error):
    batch = _batch_config().batches[0]
    task = _task(owns_manifest=owns_manifest)
    output = _output(_page(), manifest=manifest)
    if error:
        with pytest.raises(ValueError, match=error):
            _validate_task_output_ownership(batch, task, output)
    else:
        _validate_task_output_ownership(batch, task, output)


def test_unowned_page_is_not_silently_filtered():
    with pytest.raises(ValueError, match="outside owned_paths.*dashboard.yaml"):
        _validate_task_output_ownership(
            _batch_config().batches[0], _task(), _output(_page(), _page("dashboard")),
        )


@pytest.mark.asyncio
async def test_detached_page_workers_materialize_disjoint_outputs_without_root_writes():
    class Worker:
        async def ask(self, message, **kwargs):
            task = kwargs["dependencies"][CHANNEL_STATE_DEP].context_vars["current_build_task"]
            return SimpleNamespace(body=json.dumps(_output(_page(task["task_id"]))))

    context = {
        "app_build_plan": {"pages": [_page(), _page("dashboard")]},
        "app_task_batch_items": [_task(), _task("dashboard")],
    }
    await execute_task_batches_for_trigger(
        workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=_batch_config(),
        agents={"WorkerAgent": Worker()}, context_variables=context, fresh_agents_per_task=False,
    )
    assert context["app_task_batch_status"] == "completed"
    for name in ("customers", "dashboard"):
        result = context["app_task_batch_results"][name]
        assert result["manifest"] is None
        assert {entry["filename"] for entry in result["code_files"]} == {f"ui/pages/{name}.yaml"}
        assert result["_page_materialization_source"] == "app_schema_output"


def test_genesis_requires_manifest_owner_but_revision_can_reuse_baseline():
    plan = {"pages": [_page()], "build_tasks": [_task()]}
    with pytest.raises(ValueError, match="app.json"):
        validate_plan_coverage(plan, {})
    validate_plan_coverage(plan, {"build_mode": "revision"})
    plan["build_tasks"][0]["owned_paths"].append("app.json")
    validate_plan_coverage(plan, {})


def test_standalone_save_still_requires_manifest_before_any_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    with pytest.raises(ValueError, match="save_app_schema: manifest is required"):
        save_app_schema(manifest=None, pages=[_page()])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_manifest", [False, True])
async def test_partial_output_requires_complete_assembled_bundle_for_acceptance(monkeypatch, missing_manifest):
    baseline = acceptance_smoke.build_appgenerator_acceptance_files()
    baseline["provenance.yaml"] = dump_app_provenance_yaml(build_default_app_provenance(
        app_kind="generated", created_mode="factory", workflow="AppGenerator", timestamp="2026-09-12T00:00:00Z",
    ))
    page_path = next(path for path in baseline if path.startswith("ui/pages/") and path.endswith(".yaml"))
    page = yaml.safe_load(baseline[page_path])
    page["title"] = "Updated Support Tickets"
    assembled = {entry["filename"]: entry["content"] for entry in _merge_code_files([
        {"code_files": [{"filename": path, "content": content} for path, content in baseline.items()]},
        _output(page),
    ])}
    assert assembled["app.json"] == baseline["app.json"]
    assert assembled["provenance.yaml"] == baseline["provenance.yaml"]
    for path in baseline.keys() - {page_path}:
        assert assembled[path] == baseline[path]
    assert yaml.safe_load(assembled[page_path])["title"] == page["title"]
    if missing_manifest:
        assembled.pop("app.json")
    monkeypatch.setattr(acceptance_smoke, "build_appgenerator_acceptance_files", lambda *_: assembled)
    result = await acceptance_smoke.validate_appgenerator_acceptance_handoff()
    if missing_manifest:
        assert result["success"] is False
        assert result["export_gate"]["allow_export"] is False
        assert "Generated app bundles must include app.json." in json.dumps(result["acceptance"])
    else:
        assert result["success"] is True, result["validation_errors"]
        assert result["runtime_loader"]["loaded"] is True
        assert result["export_gate"]["allow_export"] is True
