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


@pytest.mark.parametrize(
    ("strategy", "required"),
    [
        ("public", False),
        ("basic-login", True),
        ("role-based", True),
        ("third-party", True),
        (None, False),
    ],
)
def test_extraction_materializes_only_canonical_auth_strategies(strategy, required):
    roles = ["admin"] if strategy == "role-based" else []
    files = extract_code_file_map_from_payload({
        "manifest": {"app_name": "Tasks", "auth_strategy": strategy, "roles": roles}, "pages": [],
    })
    assert json.loads(files["app.json"])["authRequired"] is required


@pytest.mark.parametrize("strategy", ["none", "oidc", "required", "passport-session", "", " public ", True])
def test_extraction_rejects_unknown_auth_strategy_before_materialization(strategy):
    with pytest.raises(ValueError, match="manifest.auth_strategy must be"):
        extract_code_file_map_from_payload({
            "manifest": {"app_name": "Tasks", "auth_strategy": strategy}, "pages": [],
        })


@pytest.mark.parametrize("strategy", ["public", None])
def test_extraction_rejects_roles_without_auth(strategy):
    with pytest.raises(ValueError, match="cannot be public or null when roles are declared"):
        extract_code_file_map_from_payload({
            "manifest": {"app_name": "Tasks", "auth_strategy": strategy, "roles": ["admin"]},
            "pages": [],
        })


def test_extraction_accepts_roles_with_auth():
    files = extract_code_file_map_from_payload({
        "manifest": {"app_name": "Tasks", "auth_strategy": "role-based", "roles": ["admin"]},
        "pages": [],
    })
    assert json.loads(files["app.json"])["authRequired"] is True


@pytest.mark.parametrize("role_fields", [{}, {"roles": []}])
def test_extraction_rejects_role_based_without_roles(role_fields):
    with pytest.raises(ValueError, match="requires at least one role for role-based auth"):
        extract_code_file_map_from_payload({
            "manifest": {"app_name": "Tasks", "auth_strategy": "role-based", **role_fields},
            "pages": [],
        })


def test_schema_save_materializes_public_app_without_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    save_app_schema_module.save_app_schema(
        manifest={**_base_manifest(), "auth_strategy": "public"},
        pages=[_base_page()],
        context_variables=_Context(),
    )
    paths = list(tmp_path.rglob("app.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text(encoding="utf-8"))["authRequired"] is False


@pytest.mark.parametrize("strategy", ["none", "oidc", "required", "passport-session"])
def test_schema_save_rejects_unknown_auth_strategy(tmp_path, monkeypatch, strategy):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    with pytest.raises(ValueError, match="manifest.auth_strategy must be"):
        save_app_schema_module.save_app_schema(
            manifest={**_base_manifest(), "auth_strategy": strategy},
            pages=[_base_page()],
            context_variables=_Context(),
        )
    assert not list(tmp_path.rglob("app.json"))


@pytest.mark.parametrize("strategy", ["public", None])
def test_schema_save_rejects_roles_without_auth(tmp_path, monkeypatch, strategy):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    with pytest.raises(ValueError, match="cannot be public or null when roles are declared"):
        save_app_schema_module.save_app_schema(
            manifest={**_base_manifest(), "auth_strategy": strategy, "roles": ["admin"]},
            pages=[_base_page()],
            context_variables=_Context(),
        )
    assert not list(tmp_path.rglob("app.json"))


@pytest.mark.parametrize("role_fields", [{}, {"roles": []}])
def test_schema_save_rejects_role_based_without_roles(tmp_path, monkeypatch, role_fields):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    with pytest.raises(ValueError, match="requires at least one role for role-based auth"):
        save_app_schema_module.save_app_schema(
            manifest={**_base_manifest(), "auth_strategy": "role-based", **role_fields},
            pages=[_base_page()],
            context_variables=_Context(),
        )
    assert not list(tmp_path.rglob("app.json"))


def test_schema_save_accepts_role_based_with_role(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    save_app_schema_module.save_app_schema(
        manifest={**_base_manifest(), "auth_strategy": "role-based", "roles": ["admin"]},
        pages=[_base_page()],
        context_variables=_Context(),
    )
    paths = list(tmp_path.rglob("app.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text(encoding="utf-8"))["authRequired"] is True


@pytest.mark.parametrize("approved", [None, contract("app_wide"), {"version": "1", "surfaces": []}])
def test_ownerless_public_apps_do_not_acquire_auth(approved):
    files = {"app.json": '{"authRequired":false}'}
    assert materialize_collection_auth(files, data_contract=approved) == files
