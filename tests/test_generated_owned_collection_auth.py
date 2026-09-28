from __future__ import annotations

import json

import pytest

from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import save_auth_scaffold
from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_map_from_payload,
    materialize_collection_auth,
)
from tests.test_appgenerator_save_app_schema import (
    _base_manifest,
    _base_page,
    _Context,
    save_app_schema_module,
)


def contract(tenancy="per_user", *, shared=False):
    collection = {
        "name": "tasks", "entity": "Task", "scope": "app", "tenancy": tenancy,
        "owner_field": "created_by" if tenancy != "app_wide" else None,
        "fields": [{"name": "created_by", "type": "string", "required": True}],
        "ownership": {"surface_id": "tasks", "surface_kind": "module"},
    }
    return {"version": "1", "surfaces": [] if shared else [{
        "surface_id": "tasks", "surface_kind": "module", "collections": [collection],
    }], "shared_collections": [collection] if shared else []}


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
@pytest.mark.parametrize("shared", [False, True])
def test_assembly_constructs_auth_from_approved_ownership(tenancy, shared):
    approved = contract(tenancy, shared=shared)
    files = _merge_code_files([{"code_files": [
        {"filename": "app.json", "content": '{"appName":"Tasks","authRequired":false}'},
        {"filename": "data/contract.json", "content": json.dumps(approved)},
    ]}], data_contract=approved)
    result = {item["filename"]: item["content"] for item in files}
    assert json.loads(result["app.json"])["authRequired"] is True
    assert materialize_collection_auth(result, data_contract=approved) == result


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
def test_schema_save_cannot_leave_owned_app_public(tmp_path, monkeypatch, tenancy):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    context = _Context({"data_contract": contract(tenancy)})
    save_app_schema_module.save_app_schema(
        manifest={**_base_manifest(), "auth_strategy": "public"}, pages=[_base_page()], context_variables=context,
    )
    paths = list(tmp_path.rglob("app.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text(encoding="utf-8"))["authRequired"] is True


@pytest.mark.asyncio
async def test_auth_scaffold_derives_owned_auth_before_rendering_routes():
    files = {"app.json": '{"appName":"Tasks","authRequired":false}', "data/contract.json": json.dumps(contract())}
    context = {"generated_files": files}
    result = await save_auth_scaffold(context)
    rendered = {item["filename"]: item["content"] for item in result["code_files"]}
    assert json.loads(rendered["app.json"])["authRequired"] is True
    assert {"config/auth.yaml", "ui/auth/authAdapter.js", "ui/route_manifest.json"} <= rendered.keys()


def test_public_owned_bundle_is_rejected_without_repairing_the_scanner_input():
    files = {"app.json": '{"authRequired":false}', "data/contract.json": json.dumps(contract())}
    assert any("authRequired must be true" in error for error in scan_generated_bundle(files))
    assert json.loads(files["app.json"])["authRequired"] is False


def test_extraction_derives_auth_when_owned_data_is_part_of_payload():
    files = extract_code_file_map_from_payload({
        "manifest": {"app_name": "Tasks", "auth_strategy": "public"}, "pages": [],
        "code_files": [{"filename": "data/contract.json", "content": json.dumps(contract())}],
    })
    assert json.loads(files["app.json"])["authRequired"] is True


@pytest.mark.parametrize("approved", [None, contract("app_wide"), {"version": "1", "surfaces": []}])
def test_ownerless_public_apps_do_not_acquire_auth(approved):
    files = {"app.json": '{"authRequired":false}'}
    assert materialize_collection_auth(files, data_contract=approved) == files
