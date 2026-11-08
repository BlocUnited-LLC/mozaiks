"""Page gates retain the same owner-reviewed authority through planning and replay."""
from __future__ import annotations

import hashlib
import json
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_coverage,
    review_app_build_plan,
)
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _scan_reviewed_page_roles,
)
from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import materialize_auth_scaffold
from factory_app.workflows.DesignDocs.tools import save_design_doc
from mozaiksai.core.artifacts.summary_artifacts import _summary_bytes
from mozaiksai.core.validation import GeneratedAppValidationRequest, validate_generated_app_bundle
from mozaiksai.core.validation.compilation_inputs import capture_app_compilation_inputs
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    access_policy_sha256,
    resolve_design_page_roles,
    validate_design_page_roles,
)
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from tests import test_app_compilation_inputs as compilation
from tests import test_app_plan_review as plans
from tests import test_appgenerator_save_app_schema as schemas
from tests import test_design_access_policy as designs
from tests import test_designdocs_capability_ownership as ownership
from tests import test_designdocs_monetization_inventory as inventory
from tests.factory_context import factory_context

persistence = inventory.persistence
saved = compilation.saved
POLICY = {"roles": [{"role_id": role, "label": role.title(), "description": "Verified identity claim."}
                    for role in ("member", "reviewer")], "grants": []}


def _context(roles, *, surface="declarative_page"):
    context = plans._context(surface)
    spec = detach(context.get("experience_spec"))
    spec["pages"][0]["roles"] = roles
    context.set("experience_spec", spec)
    binding = factory_context()["run_build_binding"]
    context.set("run_build_binding", binding)
    summary = {
        "access_policy": deepcopy(POLICY), "experience_spec": spec,
        "data_contract": detach(context.get("data_contract")),
        "surface_map": detach(context.get("design_surface_map")),
        "access_policy_review": {
            "status": "approved", "review_id": "owner-review", "reviewed_by": "owner",
            "target_app_id": binding["target_app_id"], "build_id": binding["build_id"],
            "policy_sha256": access_policy_sha256(POLICY, spec),
        },
    }
    record = designs._record(summary)
    record["app_id"] = binding["target_app_id"]
    context.set("design_docs_artifact", record)
    return context


@pytest.mark.parametrize("roles", [None, [], ["member"], ["reviewer", "member"]])
def test_design_plan_and_final_page_keep_existing_nullable_role_shape(roles):
    design_models, _ = load_workflow_structured_outputs("DesignDocs")
    app_models, _ = load_workflow_structured_outputs("AppGenerator")
    page = {**inventory._bundle(pricing=False)["experience_spec"]["pages"][0], "roles": roles}
    assert design_models["UIPage"].model_validate(page).roles == roles
    planned = {**plans._plan()["pages"][0], "roles": roles}
    assert app_models["AppBuildPage"].model_validate(planned).roles == roles
    assert resolve_design_page_roles(_context(roles)) == {"/reports": sorted(roles) if roles is not None else None}


def test_page_only_restriction_is_visible_and_owner_reviewed_before_save(monkeypatch, persistence):
    bundle = inventory._bundle(pricing=False)
    bundle["access_policy"] = deepcopy(POLICY)
    bundle["experience_spec"]["pages"][0]["roles"] = ["member"]
    async def approve(_tool, payload, **_kwargs):
        persistence[2].assert_not_awaited()
        assert "Page /reports: requires a verified role of Member (member)." in payload["checkpoints"]
        assert "Login does not assign roles" in payload["summary"]
        return {"action": "approve", "approved": True, "review_id": payload["review_id"]}
    ui = AsyncMock(side_effect=approve)
    monkeypatch.setattr(save_design_doc, "use_ui_tool", ui)
    context = ownership._context(managed=False)
    result = inventory._save(context, bundle)
    assert result["outcome"] == "saved", result
    ui.assert_awaited_once()
    summary = persistence[2].await_args.kwargs["summary_payload"]
    assert summary["access_policy_review"]["status"] == "approved"
    context.set("design_docs_artifact", designs._record(summary))
    assert resolve_design_page_roles(context) == {"/reports": ["member"]}


def test_unknown_page_role_fails_before_review_or_persistence(monkeypatch, persistence):
    bundle = inventory._bundle(pricing=False)
    bundle["experience_spec"]["pages"][0]["roles"] = ["invented"]
    ui = AsyncMock()
    monkeypatch.setattr(save_design_doc, "use_ui_tool", ui)
    result = inventory._save(ownership._context(managed=False), bundle)
    assert result["outcome"] == "revise" and "absent from access_policy" in result["error"]
    ui.assert_not_awaited()
    persistence[2].assert_not_awaited()


@pytest.mark.parametrize("drift", ["roles", "legacy_digest", "owner", "foreign_app", "context"])
def test_role_review_cannot_be_borrowed_or_reinterpreted(drift):
    context = _context(["member"])
    record = detach(context.get("design_docs_artifact"))
    summary = record["commit_metadata"]["metadata"]["summary_payload"]
    if drift == "roles":
        summary["experience_spec"]["pages"][0]["roles"] = []
    elif drift == "legacy_digest":
        summary["access_policy_review"]["policy_sha256"] = hashlib.sha256(
            json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    elif drift == "owner":
        summary["access_policy_review"]["reviewed_by"] = "foreign"
    elif drift == "foreign_app":
        record["app_id"] = "foreign"
    else:
        context.set("experience_spec", {"pages": [{"route": "/reports", "roles": []}]})
    context.set("design_docs_artifact", record)
    with pytest.raises(ValueError):
        resolve_design_page_roles(context)


@pytest.mark.parametrize("roles", [None, [], ["reviewer"], ["member", "reviewer"], ["unknown"]])
def test_planner_cannot_drop_or_change_reviewed_page_roles(roles):
    plan = plans._plan()
    plan["pages"][0]["roles"] = roles
    context = _context(["member"])
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision", result
    assert "must preserve reviewed roles" in result["error"]
    assert not context.get("app_plan_ready")


def test_constructed_plan_page_copies_only_reviewed_roles():
    context = _context(["member"])
    plan = plans._plan()
    plan["pages"] = []
    _repair_coverage(plan, context)
    assert plan["pages"][0]["roles"] == ["member"]


def test_planner_accepts_exact_reviewed_roles_without_granting_membership():
    plan = plans._plan()
    plan["pages"][0]["roles"] = ["member"]
    context = _context(["member"])
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    assert detach(context.get("app_build_plan"))["pages"][0]["roles"] == ["member"]


@pytest.mark.parametrize("roles", [None, [], ["member"]])
def test_save_preserves_roles_and_runtime_shell_projection(tmp_path, monkeypatch, roles):
    from mozaiksai.hosts.shell_config import _load_page_schema_routes
    context = _context(roles)
    # This page has no module bindings: the role review is independent of CRUD.
    context.set("data_contract", None)
    context.set("design_surface_map", None)
    record = detach(context.get("design_docs_artifact"))
    summary = record["commit_metadata"]["metadata"]["summary_payload"]
    summary.update(data_contract=None, surface_map=None)
    context.set("design_docs_artifact", record)
    monkeypatch.setattr(schemas.save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    page = {**schemas._base_page(), "name": "Reports", "route": "/reports", "roles": roles}
    schemas.save_app_schema_module.save_app_schema(
        manifest={**schemas._base_manifest(), "default_route": "/reports", "pages": ["Reports"]},
        pages=[page], context_variables=context,
    )
    saved_page = yaml.safe_load((tmp_path / "ui/pages/Reports.yaml").read_text(encoding="utf-8"))
    assert saved_page.get("roles") == roles
    route = _load_page_schema_routes(tmp_path)[0]
    assert route["meta"].get("requiresRole") == ("member" if roles else roles)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="must preserve reviewed roles"):
        schemas.save_app_schema_module.save_app_schema(
            manifest=schemas._base_manifest(), pages=[{**page, "roles": ["reviewer"]}], context_variables=context,
        )
    assert all(path.read_bytes() == data for path, data in before.items())


def test_custom_route_uses_existing_single_role_projection_and_rejects_drift():
    context = _context(["member"], surface="custom_react_page")
    files = {"app.json": '{"authRequired":true}', "ui/route_manifest.json": json.dumps({"pages": [{
        "path": "/reports", "component": "Reports", "meta": {"requiresRole": "member"},
    }]})}
    assert _scan_reviewed_page_roles(files, context) == []
    alias = json.loads(files["ui/route_manifest.json"])
    alias["pages"].append({"path": "/alias", "component": "Reports", "meta": {}})
    assert "no reviewed page" in _scan_reviewed_page_roles(
        {**files, "ui/route_manifest.json": json.dumps(alias)}, context,
    )[0]
    for role in (None, "reviewer"):
        files["ui/route_manifest.json"] = json.dumps({"pages": [{"path": "/reports", "meta": {"requiresRole": role}}]})
        assert "must preserve reviewed requiresRole" in _scan_reviewed_page_roles(files, context)[0]
    with pytest.raises(ValueError, match="multiple roles"):
        validate_design_page_roles({"pages": [{"route": "/reports", "roles": ["member", "reviewer"],
                                              "ui_surface": "custom_react_page"}]}, POLICY)


@pytest.mark.parametrize("key", ["roles", "requiredRole"])
def test_raw_custom_metadata_cannot_introduce_runtime_role_aliases(key):
    files = {"ui/route_manifest.json": json.dumps({"pages": [{
        "path": "/reports", "component": "Reports", "meta": {key: "reviewer"},
    }]})}
    errors = _scan_reviewed_page_roles(files, _context(None, surface="custom_react_page"))
    assert "canonical meta.requiresRole" in errors[0]


@pytest.mark.asyncio
async def test_page_only_archive_replays_exact_review_and_rejects_removed_gate(saved):
    store, context, bundle = saved
    design = store.records["design"]
    payload = design.commit_metadata.metadata["summary_payload"]
    spec = {"pages": [{"route": "/reports", "roles": ["member"], "ui_surface": "declarative_page"}]}
    payload.update(access_policy=deepcopy(POLICY), experience_spec=spec, access_policy_review={
        "status": "approved", "review_id": "owner-review", "reviewed_by": "owner", "target_app_id": "target",
        "build_id": "genesis", "policy_sha256": access_policy_sha256(POLICY, spec),
    })
    raw = _summary_bytes(payload)
    design.files_manifest[0].sha256 = hashlib.sha256(raw).hexdigest()
    design.files_manifest[0].size_bytes = len(raw)
    context["design_docs_artifact"] = design.model_dump(mode="json", by_alias=True)
    context["app_build_plan"]["capability_packs"] = []
    bundle.commit_metadata.metadata["capability_packs"] = []
    page = {**schemas._base_page(), "name": "Reports", "route": "/reports", "roles": ["member"]}
    files = {"app.json": '{"appId":"target","appName":"Reports"}',
             "ui/pages/reports.yaml": yaml.safe_dump(page),
             "data/contract.json": json.dumps(payload["data_contract"])}
    files.update(materialize_auth_scaffold(files, access_policy=POLICY))
    assert json.loads(files["app.json"])["authRequired"] is True
    auth = yaml.safe_load(files["config/auth.yaml"])
    assert auth["authorization"]["roles"] == [{"id": role, "permissions": []} for role in ("member", "reviewer")]
    path = bundle.commit_metadata.metadata["artifact_path"]
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr("bundle/" + name, content)
    sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    bundle.files_manifest[0].sha256 = sha
    bundle.files_manifest[0].size_bytes = Path(path).stat().st_size
    binding = await capture_app_compilation_inputs(context, owner_user_id="owner", archive_sha256=sha, artifact_store=store)
    assert binding is not None
    bundle.commit_metadata.metadata["compilation_inputs"] = binding
    approval = await compilation.resolve(saved)
    assert "experience_spec" not in approval.context_for("target")  # summary is the sole authority
    valid = validate_generated_app_bundle(GeneratedAppValidationRequest(files=files, approved_compilation=approval))
    assert valid.passed, valid.diagnostics
    for roles in (None, [], ["reviewer"]):
        changed = {**files, "ui/pages/reports.yaml": yaml.safe_dump({**page, "roles": roles})}
        result = validate_generated_app_bundle(GeneratedAppValidationRequest(files=changed, approved_compilation=approval))
        assert not result.passed
        assert any("must preserve reviewed roles" in item.message for item in result.diagnostics)
    missing = validate_generated_app_bundle(GeneratedAppValidationRequest(files={"app.json": files["app.json"]}, approved_compilation=approval))
    assert any("missing routes" in item.message for item in missing.diagnostics)
    disabled = {**files, "app.json": '{"appId":"target","authRequired":false}'}
    result = validate_generated_app_bundle(GeneratedAppValidationRequest(files=disabled, approved_compilation=approval))
    assert any("Reviewed page roles require" in item.message for item in result.diagnostics)
    for alias_path in ("/alias", auth["routes"]["login"]):
        manifest = json.loads(files["ui/route_manifest.json"])
        manifest["pages"] = [entry for entry in manifest["pages"] if entry["path"] != alias_path]
        manifest["pages"].append({
            "path": alias_path, "component": "SchemaPage", "schema": "reports", "meta": {"requiresAuth": False},
        })
        aliased = {**files, "ui/route_manifest.json": json.dumps(manifest)}
        result = validate_generated_app_bundle(GeneratedAppValidationRequest(files=aliased, approved_compilation=approval))
        assert not result.passed
        assert any("no reviewed page in ExperienceSpec" in item.message for item in result.diagnostics)
    for alternate_meta in ({"roles": "reviewer"}, {"requiredRole": "reviewer"}, {"requiresRole": []}):
        manifest = json.loads(files["ui/route_manifest.json"])
        login = next(entry for entry in manifest["pages"] if entry["path"] == auth["routes"]["login"])
        login["meta"].update(alternate_meta)
        result = validate_generated_app_bundle(GeneratedAppValidationRequest(
            files={**files, "ui/route_manifest.json": json.dumps(manifest)}, approved_compilation=approval,
        ))
        assert not result.passed
        assert any("Reviewed page role restrictions" in item.message for item in result.diagnostics)
    alias_page = {**files, "ui/pages/alias.yaml": yaml.safe_dump({**page, "name": "Alias", "route": "/alias", "roles": None})}
    result = validate_generated_app_bundle(GeneratedAppValidationRequest(files=alias_page, approved_compilation=approval))
    assert any("no reviewed page in ExperienceSpec" in item.message for item in result.diagnostics)
    # An authored upload has no claim to Factory approval and uses static validation.
    assert validate_generated_app_bundle(GeneratedAppValidationRequest(files=files)).passed
    for component in ("LoginPage", "AuthCallbackPage"):
        shadowed = {
            **files,
            "ui/index.js": (
                "import Reports from './pages/custom/Reports.jsx';\n"
                "export function register(registerComponent) {\n"
                f"  registerComponent('{component}', Reports);\n"
                "}\n"
            ),
            "ui/pages/custom/Reports.jsx": "export default function Reports() { return <div>Private reports</div>; }\n",
        }
        result = validate_generated_app_bundle(GeneratedAppValidationRequest(files=shadowed, approved_compilation=approval))
        assert not result.passed
        assert any("no reviewed page in ExperienceSpec" in item.message for item in result.diagnostics)
        # Explicit authored auth UI overrides remain supported outside Factory approval.
        authored = validate_generated_app_bundle(GeneratedAppValidationRequest(files=shadowed))
        assert authored.passed, authored.diagnostics
