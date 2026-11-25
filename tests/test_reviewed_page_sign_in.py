"""A page's sign-in decision survives plan review, generation, and final admission."""
import json

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import (
    _apply_planned_page_contracts,
)
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _scan_reviewed_page_roles,
)
from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import materialize_auth_scaffold
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_entries_from_payload,
)
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    access_policy_sha256,
    project_reviewed_page_access,
    resolve_scaffold_access,
    validate_page_role_preservation,
)
from mozaiksai.hosts.shell_config import _load_page_schema_routes, _normalize_shell_page_entry
from tests import test_app_plan_review as plans
from tests import test_appgenerator_save_app_schema as schemas
from tests import test_reviewed_page_roles as reviewed


def _context(requires_auth, *, roles=None, surface="declarative_page"):
    context = reviewed._context(roles, surface=surface)
    record = detach(context.get("design_docs_artifact"))
    summary = record["commit_metadata"]["metadata"]["summary_payload"]
    spec = summary["experience_spec"]
    spec["pages"][0]["requiresAuth"] = requires_auth
    context.set("experience_spec", spec)
    # Keep collection policy independent of the page-only sign-in decision.
    for source, target in (("data_contract", "data_contract"), ("design_surface_map", "surface_map")):
        summary[target] = None
        context.set(source, None)
    if not roles:
        summary["access_policy"]["roles"] = []
    summary["access_policy_review"]["policy_sha256"] = access_policy_sha256(summary["access_policy"], spec)
    context.set("design_docs_artifact", record)
    return context


def test_planner_projects_public_choice_and_rejects_an_explicit_change():
    context = reviewed._context([])
    record = detach(context.get("design_docs_artifact"))
    summary = record["commit_metadata"]["metadata"]["summary_payload"]
    spec = summary["experience_spec"]
    spec["pages"][0]["requiresAuth"] = False
    summary["access_policy_review"]["policy_sha256"] = access_policy_sha256(summary["access_policy"], spec)
    context.set("design_docs_artifact", record)
    context.set("experience_spec", spec)
    plan = plans._plan()
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    assert detach(context.get("app_build_plan"))["pages"][0]["requiresAuth"] is False
    plan["pages"][0]["requiresAuth"] = True
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision" and "reviewed requiresAuth=false" in result["error"]


@pytest.mark.parametrize(("gate", "roles"), [(False, []), (True, []), (True, ["member"])])
def test_direct_save_projects_exact_review_into_runtime_page_and_manifest(tmp_path, monkeypatch, gate, roles):
    context = _context(gate, roles=roles)
    monkeypatch.setattr(schemas.save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    page = {**schemas._base_page(), "name": "Reports", "route": "/reports"}
    manifest = {**schemas._base_manifest(), "auth_strategy": "public", "roles": [],
                "default_route": "/reports", "pages": ["Reports"]}
    schemas.save_app_schema_module.save_app_schema(manifest=manifest, pages=[page], context_variables=context)
    assert json.loads((tmp_path / "app.json").read_text())["authRequired"] is gate
    route = _load_page_schema_routes(tmp_path)[0]
    assert route["meta"]["requiresAuth"] is gate
    assert route["meta"].get("requiresRole") == ("member" if roles else [])
    path = tmp_path / "ui/pages/Reports.yaml"
    original = path.read_bytes()
    with pytest.raises(ValueError, match="reviewed requiresAuth"):
        schemas.save_app_schema_module.save_app_schema(
            manifest=manifest, pages=[{**page, "meta": {"requiresAuth": not gate}}], context_variables=context,
        )
    assert path.read_bytes() == original


def test_custom_projection_only_changes_owned_route_and_final_scanner_rejects_drift():
    context = _context(False, roles=[], surface="custom_react_page")
    manifest = "ui/route_manifest.json"
    owned = {"path": "/reports", "component": "Reports"}
    sibling = {"path": "/login", "component": "LoginPage", "meta": {"requiresAuth": False}}
    result = project_reviewed_page_access(
        {manifest: json.dumps({"pages": [owned, sibling]})}, context,
        owned_paths={manifest}, owned_routes={"/reports"},
    )
    route, unchanged = json.loads(result[manifest])["pages"]
    assert _normalize_shell_page_entry(route, order_fallback=0)["meta"]["requiresAuth"] is False
    assert unchanged == sibling
    assert _scan_reviewed_page_roles({manifest: json.dumps({"pages": [route]})}, context) == []
    for wrong in (owned, {**route, "requiresAuth": True}, {**route, "meta": {"requiresAuth": True}}):
        assert "requiresAuth" in _scan_reviewed_page_roles({manifest: json.dumps({"pages": [wrong]})}, context)[0]


@pytest.mark.parametrize("surface", ["plan", "schema", "custom"])
@pytest.mark.parametrize("pre_policy", [False, True])
def test_new_public_route_requires_fresh_review_for_older_designs(surface, pre_policy):
    context = reviewed._context([])
    if pre_policy:
        record = detach(context.get("design_docs_artifact"))
        summary = record["commit_metadata"]["metadata"]["summary_payload"]
        del summary["access_policy"], summary["access_policy_review"]
        context.set("design_docs_artifact", record)
    pages = [{"route": "/reports", "roles": []}]
    custom = []
    if surface == "plan":
        pages[0]["requiresAuth"] = False
    elif surface == "schema":
        pages[0]["meta"] = {"requiresAuth": False}
    else:
        pages = []
        custom = [{"path": "/reports", "requiresAuth": False}]
    kwargs = {"planned": surface == "plan", "custom_routes": custom}
    # A historical receipt still verifies historical bytes on replay.
    assert validate_page_role_preservation(pages, context, **kwargs) == (None if pre_policy else {"/reports": []})
    with pytest.raises(ValueError, match="new DesignDocs sign-in review"):
        validate_page_role_preservation(pages, context, fill_missing=True, **kwargs)


def test_final_scanner_and_scaffold_require_login_for_roleless_signed_in_page():
    context = _context(True, roles=[])
    policy, required = resolve_scaffold_access(context)
    files = {"app.json": '{"authRequired": false}',
             "ui/pages/reports.yaml": yaml.safe_dump({"name": "Reports", "route": "/reports", "roles": []})}
    assert "app.json.authRequired=true" in _scan_reviewed_page_roles(files, context)[0]
    files.update(materialize_auth_scaffold(files, access_policy=policy, requires_sign_in=required))
    assert json.loads(files["app.json"])["authRequired"] is True
    assert _scan_reviewed_page_roles(files, context) == []
    manifest = json.loads(files["ui/route_manifest.json"])
    login = next(entry for entry in manifest["pages"] if entry["component"] == "LoginPage")
    login["requiresAuth"] = True
    files["ui/route_manifest.json"] = json.dumps(manifest)
    assert "no reviewed page" in _scan_reviewed_page_roles(files, context)[0]


def test_assembly_reprojects_review_after_rematerializing_an_intact_typed_worker_response():
    context = _context(False, roles=[])
    path = "ui/pages/reports.yaml"
    page = {**schemas._base_page(), "name": "Reports", "route": "/reports"}
    authored = {"manifest": None, "pages": [page], "custom_route_bundle": None}
    approved = project_reviewed_page_access(
        {path: yaml.safe_dump(page)}, context, owned_paths={path},
    )
    # The accepted packet includes both the raw typed fields and their compiled files.
    entries = extract_code_file_entries_from_payload({
        **authored, "code_files": [{"filename": path, "content": approved[path]}],
    })
    assert "requiresAuth" not in (yaml.safe_load(entries[0]["content"]).get("meta") or {})
    plan = {"pages": [{"name": "Reports", "route": "/reports", "ui_surface": "declarative_page"}],
            "build_tasks": [{"task_id": "reports", "task_type": "page_bundle", "owned_paths": [path]}]}
    result = _apply_planned_page_contracts(entries, plan, context_variables=context)
    shipped = {row["filename"]: row["content"] for row in result}
    assert yaml.safe_load(shipped[path])["meta"]["requiresAuth"] is False
    assert _scan_reviewed_page_roles(shipped, context) == []
