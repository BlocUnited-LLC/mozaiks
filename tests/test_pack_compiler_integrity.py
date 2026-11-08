"""Approved deterministic pack compilation must preserve source integrity."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from factory_app.workflows.AppGenerator.tools.module_entitlement_gates import (
    apply_entitlement_gates,
)
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_managed_capability_templates,
    verify_pack_integrity,
)
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.module_loader import ModuleAdminManifest
from mozaiksai.core.runtime.app.page_schema import AppPageSection
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outcome_feedback import WorkflowFeedbackEvidence
from tests.appgenerator_subscription_fixture import subscription_contract
from tests.factory_context import factory_context

ROOT = Path(__file__).resolve().parents[1]


class Context(dict):
    def set(self, key, value):
        self[key] = value


def _context(pack_id, *, subscription=None):
    root = ROOT / "factory_app/build_context" / pack_id
    metadata = verify_pack_integrity(root, pack_id)
    row = {"id": pack_id, "capability_source": "generated_module", "pack_source_path": str(root),
           **{key: metadata[key] for key in ("version", "digest")}}
    contract = {"version": "1", "surfaces": [], "shared_collections": []}
    entities = []
    if pack_id == "outcome_feedback":
        entities = ["WorkflowFeedback"]
        contract["surfaces"] = [{"surface_id": pack_id, "surface_kind": "module", "collections": [{
            "name": "records", "entity": "WorkflowFeedback", "scope": "app", "tenancy": "per_user",
            "owner_field": "user_id", "ownership": {"surface_id": pack_id, "surface_kind": "module"},
            "fields": [{"name": field, "type": "object" if field == "response" else "string", "required": True}
                       for field in [*WorkflowFeedbackEvidence.model_fields, "feedback_id"]],
            "lifecycle": {"write_mode": "workflow_write", "migration_policy": "additive_only"},
            "search_by": "feedback_id", "indexes": [],
        }]}]
    design = {"surfaces": [{
        "surface_id": "commerce", "surface_kind": "module", "owner": "app",
        "primary_entities": [],
        "owned_mutations": ["create_product", "update_product", "archive_product", "adjust_inventory", "add_cart_item",
                            "update_cart_item", "remove_cart_item", "clear_cart", "start_checkout",
                            "record_checkout_result", "update_order_status"],
        "custom_reads": ["list_products", "get_product", "get_cart", "list_orders", "get_order"],
    }]} if pack_id == "commerce" else None
    return Context(factory_context({
        "app_id": "pack-compiler-proof", "capability_packs": [row], "data_contract": contract,
        "app_build_plan": {"capability_packs": [{"capability_pack_id": pack_id,
                            "capability_source": "generated_module", "primary_entities": entities}]},
        "subscription_contract": subscription,
        "design_surface_map": design,
        "generated_files": {"data/contract.json": json.dumps(contract),
                            "app.json": json.dumps({"appId": "pack-compiler-proof", "appName": "Pack proof", "authRequired": True})},
        "build_timestamp": "2026-11-08T00:00:00Z",
    }))


async def _assemble(context):
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    return {file["filename"]: file["content"] for file in result["code_files"]}


def _scan(files, context):
    return scan_generated_bundle(
        files, capability_packs=detach(context["capability_packs"]),
        planned_data_contract=detach(context["data_contract"]),
        subscription_contract=detach(context.get("subscription_contract")), context_variables=context,
    )


def _commerce_modules(context):
    # Exercise module compilation independently of commerce's legacy migration
    # correction, which is owned by the pack persistence change.
    files = resolve_managed_capability_templates(context["capability_packs"], context_variables=context)
    compiled = _merge_code_files(
        [{"code_files": [file for file in files if file["filename"].startswith("modules/")]}],
        app_build_plan=context["app_build_plan"], data_contract=context["data_contract"],
        subscription_contract=context.get("subscription_contract"), context_variables=context,
    )
    compiled = apply_entitlement_gates(compiled, context_variables=context)
    return {file["filename"]: file["content"] for file in [*files, *compiled]}


@pytest.mark.asyncio
async def test_feedback_compilation_assembles_twice_and_preserves_pack_integrity():
    context = _context("outcome_feedback")
    first = await _assemble(context)
    assert await _assemble(context) == first
    assert _scan(first, context) == []
    actions = {action["id"] for action in yaml.safe_load(first["modules/outcome_feedback/module.yaml"])["actions"]}
    assert actions == {"record_workflow_feedback", "list_my_feedback", "get_records", "list_records"}
    assert not any(action.startswith(("create_", "update_", "delete_")) for action in actions)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["module.yaml", "backend/handler.py", "backend/service.py", "backend/repo.py"])
async def test_feedback_candidate_changes_cannot_authorize_themselves(path):
    context = _context("outcome_feedback")
    files = await _assemble(context)
    target = "modules/outcome_feedback/" + path
    files[target] += "\n# unauthorized candidate change\n"
    context["generated_files"] = deepcopy(files)
    assert any(error.startswith(target + ": differs from the verified template") for error in _scan(files, context))


@pytest.mark.asyncio
async def test_feedback_compiled_reads_require_original_approved_inputs():
    context = _context("outcome_feedback")
    files = await _assemble(context)
    errors = scan_generated_bundle(files, capability_packs=context["capability_packs"])
    assert any("differs from the verified template" in error for error in errors)


@pytest.mark.asyncio
async def test_feedback_candidate_cannot_add_an_unapproved_read():
    context = _context("outcome_feedback")
    files = await _assemble(context)
    path = "modules/outcome_feedback/module.yaml"
    manifest = yaml.safe_load(files[path])
    action = deepcopy(next(action for action in manifest["actions"] if action["id"] == "list_records"))
    action.update(id="list_everyones_feedback", handler_method="list_everyones_feedback")
    manifest["actions"].append(action)
    files[path] = yaml.safe_dump(manifest, sort_keys=False)
    context["generated_files"] = deepcopy(files)
    assert any(error.startswith(path + ": differs from the verified template") for error in _scan(files, context))


def test_commerce_admin_panels_use_loadable_schema_sections_and_declared_actions():
    root = ROOT / "factory_app/build_context/commerce/templates/modules/commerce"
    manifest = ModuleAdminManifest.model_validate(yaml.safe_load((root / "contracts/admin.yaml").read_text(encoding="utf-8")))
    actions = {action["id"] for action in yaml.safe_load((root / "module.yaml").read_text(encoding="utf-8"))["actions"]}
    assert len(manifest.panels) == 3
    assert {panel.page for panel in manifest.panels} == {"commerce"}
    assert all(panel.permissions and "ops.admin" in panel.permissions for panel in manifest.panels)
    endpoints = set()
    for panel in manifest.panels:
        for section in panel.sections:
            AppPageSection.model_validate(section)
            config = section["config"]
            endpoint = config.get("api_endpoint") or config["submit_action"]["href"]
            assert endpoint.startswith("/api/modules/commerce/")
            endpoints.add(endpoint.rsplit("/", 1)[1])
    assert endpoints <= actions
    assert {"create_product", "update_product", "archive_product", "adjust_inventory", "update_order_status"} <= endpoints


@pytest.mark.asyncio
@pytest.mark.parametrize("gated", [False, True])
async def test_commerce_compiles_only_approved_pricing_gates(gated):
    contract = subscription_contract("commerce", "create_product", "start_checkout") if gated else None
    if contract:
        contract["subscription_config_file"]["label"] = "Commerce plans"
    context = _context("commerce", subscription=contract)
    files = _commerce_modules(context)
    assert _commerce_modules(context) == files
    errors = _scan(files, context)
    assert not any("verified template" in error or "approved compilation failed" in error for error in errors), errors
    actions = {action["id"]: action for action in yaml.safe_load(files["modules/commerce/module.yaml"])["actions"]}
    assert {name: row["entitlement_gate"] for name, row in actions.items() if row.get("entitlement_gate")} == (
        {name: "feature.module.commerce." + name for name in ("create_product", "start_checkout")} if gated else {}
    )
    if gated:
        for default_plan, granted in (("free", False), ("pro", True)):
            adapter = ConfiguredEntitlementAdapter(config=SubscriptionsConfig.model_validate({
                **contract["subscription_config_file"], "default_plan_id": default_plan,
            }))
            for action in ("create_product", "start_checkout"):
                result = await adapter.check(actions[action]["entitlement_gate"], app_id="pack-compiler-proof", user_id="member")
                assert result.granted is granted
        manifest = yaml.safe_load(files["modules/commerce/module.yaml"])
        next(action for action in manifest["actions"] if action["id"] == "create_product").pop("entitlement_gate")
        files["modules/commerce/module.yaml"] = yaml.safe_dump(manifest, sort_keys=False)
        context["generated_files"] = deepcopy(files)
        assert any("modules/commerce/module.yaml: differs from the verified template" in error for error in _scan(files, context))


def test_commerce_candidate_cannot_inject_a_gate_or_change_permissions():
    context = _context("commerce")
    original = _commerce_modules(context)
    path = "modules/commerce/module.yaml"
    for change in ({"entitlement_gate": "commerce.unapproved"}, {"permissions": []}):
        files = deepcopy(original)
        manifest = yaml.safe_load(files[path])
        next(action for action in manifest["actions"] if action["id"] == "create_product").update(change)
        files[path] = yaml.safe_dump(manifest, sort_keys=False)
        context["generated_files"] = deepcopy(files)
        errors = _scan(files, context)
        assert any("approved compilation failed" in error or error.startswith(path + ": differs from the verified template")
                   for error in errors), errors


def test_compiler_integrity_rejects_colliding_verified_pack_sources(tmp_path):
    selected = []
    path = "modules/collision/module.yaml"
    for name in ("first", "second"):
        root = tmp_path / name
        target = root / "templates" / path
        target.parent.mkdir(parents=True)
        target.write_text(yaml.safe_dump({"schema_version": "mozaiks.module.v1", "module": {
            "id": "collision", "display_name": name, "version": "1.0.0", "handler": "backend.handler:Handler",
        }, "actions": [], "permissions": []}), encoding="utf-8")
        (root / "context.yaml").write_text(yaml.safe_dump({
            "context_id": name, "applies_to_workflows": ["AppGenerator"],
            "assets": [{"path": "contract.yaml", "kind": "contract"}, {"path": "templates/", "kind": "templates"}],
            "pack": {"id": name, "version": "0.1.0", "status": "active", "capability_source": "generated_module"},
        }), encoding="utf-8")
        (root / "contract.yaml").write_text(yaml.safe_dump({
            "contract_id": name, "contract_type": "build_pack_instructions",
            "required_outputs": [{"path": path, "owner": "templates"}],
        }), encoding="utf-8")
        metadata = verify_pack_integrity(root, name)
        selected.append({"id": name, "pack_source_path": str(root),
                         **{key: metadata[key] for key in ("version", "digest")}})
    context = {"capability_packs": selected, "data_contract": {"version": "1", "surfaces": [], "shared_collections": []}}
    errors = scan_generated_bundle({path: target.read_text(encoding="utf-8")}, capability_packs=selected,
                                   planned_data_contract=context["data_contract"], context_variables=context)
    assert any("both declare output path '" + path in error and "fail closed" in error for error in errors), errors
