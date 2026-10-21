from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from ag2.network.policies import CHANNEL_STATE_DEP
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.app_plan_review import validate_plan_coverage
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import (
    _apply_planned_page_contracts,
)
from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema
from mozaiksai.core.runtime.app.provenance import (
    build_default_app_provenance,
    dump_app_provenance_yaml,
)
from mozaiksai.core.workflow.generator_support.code_files import (
    discard_pack_owned_outputs,
    extract_code_file_map_from_payload,
)
from mozaiksai.core.workflow.outputs.structured import (
    build_models_from_config,
    get_provider_response_model,
)
from mozaiksai.core.workflow.task_batches import (
    _normalize_owned_page_files_from_plan,
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
    expected_page = _page()
    expected_page.pop("roles")
    expected_page.pop("navigation")
    assert yaml.safe_load(files["ui/pages/customers.yaml"]) == expected_page
    assert json.dumps(payload, sort_keys=True) == before


def test_typed_custom_bundle_builds_runtime_route_manifest_and_registry(schema_model):
    from tests.test_appgenerator_save_app_schema import _custom_route_bundle

    output = _output()
    output["custom_route_bundle"] = _custom_route_bundle()
    # The declared output has no ui_index field; both save paths must derive it.
    typed = schema_model.model_validate(output).model_dump(mode="json")
    files = extract_code_file_map_from_payload(typed)
    assert set(files) == {"ui/route_manifest.json", "ui/index.js", "ui/pages/custom/InvestorDealRoom.jsx"}
    routes = json.loads(files["ui/route_manifest.json"])
    assert routes["pages"][0]["path"] == "/deal-room"
    assert "import InvestorDealRoom from './pages/custom/InvestorDealRoom';" in files["ui/index.js"]
    assert "registerComponent('InvestorDealRoomPage', InvestorDealRoom" in files["ui/index.js"]
    _validate_task_output_ownership(
        _batch_config().batches[0], {**_task(), "owned_paths": list(files)}, typed,
    )


def _planned_custom_candidate(schema_model):
    from tests.test_appgenerator_save_app_schema import _custom_route_bundle

    output = _output()
    output["custom_route_bundle"] = _custom_route_bundle()
    output["custom_route_bundle"]["page_files"][0]["path"] = "ui/pages/custom/deal_room.jsx"
    typed = schema_model.model_validate(output).model_dump(mode="json")
    files = extract_code_file_map_from_payload(typed)
    task = {**_task(), "task_id": "custom_pages", "owned_paths": list(files)}
    plan = {"pages": [{"name": "Deal room", "route": "/deal-room", "ui_surface": "custom_react_page"}],
            "build_tasks": [task]}
    return typed, task, plan


@pytest.mark.parametrize("stage", ["task", "standalone"])
@pytest.mark.parametrize("path", ["ui/index.js", "ui/route_manifest.json", "ui/pages/custom/../../index.jsx"])
def test_custom_page_files_cannot_replace_derived_registry(schema_model, stage, path):
    from tests.test_appgenerator_save_app_schema import save_app_schema_module

    typed, _, _ = _planned_custom_candidate(schema_model)
    page_files = typed["custom_route_bundle"]["page_files"]
    # The live failure had one valid page plus an extra authored registry file.
    page_files.append({**page_files[0], "path": path, "component_name": "registry",
                       "content": "export default {};"})
    before = deepcopy(typed)
    with pytest.raises(ValueError, match="page_files.*ui/pages/custom/.*ui/index.js.*generated.*omit"):
        if stage == "task":
            extract_code_file_map_from_payload(typed)
        else:
            save_app_schema_module._validate_custom_route_bundle(typed["custom_route_bundle"])
    assert typed == before


def test_custom_registry_feedback_allows_corrected_page_retry(schema_model):
    typed, task, plan = _planned_custom_candidate(schema_model)
    page_files = typed["custom_route_bundle"]["page_files"]
    original_page = deepcopy(page_files[0])
    page_files.append({**original_page, "path": "ui/index.js", "component_name": "registry",
                       "content": "export default {};"})
    with pytest.raises(ValueError, match="ui/index.js.*generated.*omit"):
        extract_code_file_map_from_payload(typed)

    # A new worker response follows the diagnostic; runtime never drops code.
    corrected = deepcopy(typed)
    corrected["custom_route_bundle"]["page_files"] = [original_page]
    files = extract_code_file_map_from_payload(schema_model.model_validate(corrected).model_dump(mode="json"))
    admitted = _normalize_owned_page_files_from_plan(
        [{"filename": path, "content": content} for path, content in files.items()],
        task=task, base_context={"app_build_plan": plan},
    )
    assert {entry["filename"]: entry["content"] for entry in admitted} == files
    assert files[original_page["path"]] == original_page["content"]
    assert "import InvestorDealRoom from './pages/custom/deal_room';" in files["ui/index.js"]
    assert json.loads(files["ui/route_manifest.json"])["pages"][0]["path"] == "/deal-room"
    assert len(typed["custom_route_bundle"]["page_files"]) == 2


@pytest.mark.parametrize("stage", ["admission", "assembly"])
@pytest.mark.parametrize("mutation", [None, "route", "extra_route", "wrong_file_binding", "missing_file"])
def test_custom_plan_binding_closes_typed_candidates_at_both_owners(schema_model, stage, mutation):
    typed, task, plan = _planned_custom_candidate(schema_model)
    bundle = typed["custom_route_bundle"]
    if mutation == "route":
        bundle["route_manifest"][0]["path"] = "/invented"
    elif mutation == "extra_route":
        bundle["route_manifest"].append({**bundle["route_manifest"][0], "id": "invented", "path": "/invented"})
    files = extract_code_file_map_from_payload(schema_model.model_validate(typed).model_dump(mode="json"))
    if mutation == "wrong_file_binding":
        files["ui/index.js"] = files["ui/index.js"].replace("./pages/custom/deal_room", "./pages/custom/other")
        files["ui/pages/custom/other.jsx"] = "export default function Other() { return null; }"
    elif mutation == "missing_file":
        files.pop("ui/pages/custom/deal_room.jsx")
    entries = [{"filename": path, "content": content} for path, content in files.items()]

    def validate():
        if stage == "admission":
            return _normalize_owned_page_files_from_plan(entries, task=task, base_context={"app_build_plan": plan})
        return _apply_planned_page_contracts(entries, plan)

    if mutation:
        with pytest.raises(ValueError, match="unapproved custom route|canonical page file|owned custom page"):
            validate()
    else:
        assert {entry["filename"]: entry["content"] for entry in validate()} == files


@pytest.mark.parametrize("custom_auth_paths", [False, True])
def test_authenticated_custom_page_assembly_keeps_canonical_auth_routes(schema_model, custom_auth_paths):
    typed, _, plan = _planned_custom_candidate(schema_model)
    typed["manifest"] = {**_manifest(), "auth_strategy": "basic-login", "default_route": "/deal-room", "pages": []}
    entries = _merge_code_files([schema_model.model_validate(typed).model_dump(mode="json")], app_build_plan=plan)
    files = {entry["filename"]: entry["content"] for entry in entries}
    routes = json.loads(files["ui/route_manifest.json"])["pages"]
    assert {route["path"] for route in routes} == {"/deal-room", "/login", "/auth/callback"}
    if custom_auth_paths:
        from factory_app.workflows.AppGenerator.tools.code_file_utils import (
            compose_bundle_auth_routes,
        )

        auth = yaml.safe_load(files["config/auth.yaml"])
        auth["routes"]["login"] = "/sign-in"
        auth["routes"]["callback"] = "/sign-in/callback"
        files["config/auth.yaml"] = yaml.safe_dump(auth)
        files["ui/route_manifest.json"] = json.dumps({"pages": [routes[0]]})
        compose_bundle_auth_routes(files)
        entries = [{"filename": path, "content": content} for path, content in files.items()]
    assert {entry["filename"]: entry["content"] for entry in _apply_planned_page_contracts(entries, plan)} == files


@pytest.mark.parametrize("mutation", ["component", "auth_meta", "extra", "duplicate", "missing_contract", "public_manifest"])
def test_custom_route_admission_cannot_bypass_plan_by_claiming_auth(schema_model, mutation):
    from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import (
        materialize_auth_scaffold,
    )

    typed, task, plan = _planned_custom_candidate(schema_model)
    files = extract_code_file_map_from_payload(typed)
    files["app.json"] = json.dumps({"name": "Example", "authRequired": True})
    files.update(materialize_auth_scaffold(files))
    manifest = json.loads(files["ui/route_manifest.json"])
    login = next(route for route in manifest["pages"] if route["path"] == "/login")
    if mutation == "component":
        login["component"] = "UnapprovedCustomLogin"
    elif mutation == "auth_meta":
        login["meta"]["requiresAuth"] = True
    elif mutation == "extra":
        manifest["pages"].append({**login, "path": "/unapproved"})
    elif mutation == "duplicate":
        manifest["pages"].append(deepcopy(login))
    elif mutation == "missing_contract":
        files.pop("config/auth.yaml")
    else:
        files["app.json"] = json.dumps({"name": "Example", "authRequired": False})
    files["ui/route_manifest.json"] = json.dumps(manifest)
    entries = [{"filename": path, "content": content} for path, content in files.items()]
    with pytest.raises(ValueError, match="unapproved custom route") as error:
        _normalize_owned_page_files_from_plan(entries, task=task, base_context={"app_build_plan": plan})
    assert "approved" in str(error.value) and "/deal-room" in str(error.value)
    assert "canonical auth" in str(error.value)


@pytest.mark.parametrize("stage", ["admission", "assembly"])
@pytest.mark.parametrize("change_baseline_route", [False, True])
@pytest.mark.parametrize("build_mode", ["revision", "initial"])
def test_scoped_custom_revision_retains_only_unchanged_baseline_routes(schema_model, stage, change_baseline_route, build_mode):
    typed, task, plan = _planned_custom_candidate(schema_model)
    baseline_route = {**typed["custom_route_bundle"]["route_manifest"][0],
                      "id": "existing", "path": "/existing", "component": "ExistingPage"}
    baseline = {"ui/route_manifest.json": json.dumps({"pages": [baseline_route]})}
    preserved = deepcopy(baseline_route)
    if change_baseline_route:
        preserved["component"] = "InvestorDealRoomPage"
    typed["custom_route_bundle"]["route_manifest"].append(preserved)
    files = extract_code_file_map_from_payload(schema_model.model_validate(typed).model_dump(mode="json"))
    entries = [{"filename": path, "content": content} for path, content in files.items()]
    context = {"app_build_plan": plan, "generated_files": baseline, "build_mode": build_mode}

    def validate():
        if stage == "admission":
            return _normalize_owned_page_files_from_plan(entries, task=task, base_context=context)
        return _apply_planned_page_contracts(entries, plan, context_variables=context)

    if change_baseline_route or build_mode != "revision":
        with pytest.raises(ValueError, match="unapproved custom route '/existing'"):
            validate()
    else:
        assert {entry["filename"]: entry["content"] for entry in validate()} == files


@pytest.mark.parametrize("pack_paths", [
    frozenset({"ui/index.js"}), frozenset({"ui/route_manifest.json"}),
    frozenset({"ui/index.js", "ui/route_manifest.json", "ui/pages/custom/deal_room.jsx"}),
])
def test_pack_filter_removes_derived_custom_registries_without_ui_index_field(schema_model, pack_paths):
    typed, _, _ = _planned_custom_candidate(schema_model)
    before = deepcopy(typed)
    original = extract_code_file_map_from_payload(typed)
    filtered, dropped = discard_pack_owned_outputs(typed, pack_paths)
    assert set(dropped) == pack_paths
    assert extract_code_file_map_from_payload(filtered) == {
        path: content for path, content in original.items() if path not in pack_paths
    }
    assert typed == before


@pytest.mark.asyncio
@pytest.mark.parametrize("route,expected_status", [("/deal-room", "completed"), ("/invented", "failed")])
async def test_detached_custom_worker_rejects_unapproved_route_before_admission(schema_model, route, expected_status):
    typed, task, plan = _planned_custom_candidate(schema_model)
    typed["custom_route_bundle"]["route_manifest"][0]["path"] = route

    class Worker:
        async def ask(self, message, **kwargs):
            return SimpleNamespace(body=json.dumps(typed))

    context = {"app_build_plan": plan, "app_task_batch_items": [task]}
    async def execute():
        await execute_task_batches_for_trigger(
            workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=_batch_config(),
            agents={"WorkerAgent": Worker()}, context_variables=context, fresh_agents_per_task=False,
        )

    if route == "/invented":
        with pytest.raises(RuntimeError, match="unapproved custom route"):
            await execute()
    else:
        await execute()
    assert context["app_task_batch_status"] == expected_status
    results = context["app_task_batch_results"]
    if route == "/invented":
        assert "unapproved custom route" in json.dumps(results)
        assert "custom_pages" not in results
    else:
        assert "custom_pages" in results


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
    ], data_contract=json.loads(baseline["data/contract.json"]))}
    assert assembled["app.json"] == baseline["app.json"]
    assert assembled["provenance.yaml"] == baseline["provenance.yaml"]
    for path in baseline.keys() - {page_path}:
        if path == "data/contract.json":
            assert json.loads(assembled[path]) == json.loads(baseline[path])
        else:
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
        assert result["success"] is False
        assert result["runtime_loader"]["loaded"] is True
        assert result["export_gate"]["allow_export"] is False
        assert result["acceptance"]["status"] == "pending"
        evidence = result["acceptance"]["validation_evidence"]
        assert evidence["failed"] == []
        assert evidence["skipped"] == ["app_runtime_load_worker", "app_runtime_smoke"]
        assert "workflow_integration" in evidence["completed"]
        assert "snapshot_digest" not in result["acceptance"]
        assert result["app_validation_result"]["validation_status"] == "pending"
        assert result["context"]["integration_tests_passed"] is False
