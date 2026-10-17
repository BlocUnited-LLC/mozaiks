"""Verified template persistence joins the admitted app without generic CRUD."""
from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest
import yaml
from bson import ObjectId

from factory_app.workflows.AppGenerator.tools.app_validation import (
    run_app_bundle_acceptance_gate,
    validate_module_implementation_contract,
)
from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _scan_pack_provenance_manifest,
    scan_generated_bundle,
)
from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
    audit_module_runtime_quality,
)
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    PackIntegrityError,
    resolve_managed_capability_templates,
    verified_pack_data_contracts,
    verified_pack_template_inventory,
    verified_template_module_ids,
    verify_pack_integrity,
)
from mozaiksai.core.account import AccountDataRegistry
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.persistence import apply_data_migrations, load_data_migrations
from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal
from mozaiksai.core.runtime.persistence.mongo import (
    DEFAULT_APP_DATABASE_NAME,
    MongoPersistenceContext,
)
from mozaiksai.core.workflow.generator_support.code_files import compile_data_contract
from tests.test_appgenerator_revision_baseline import _replace_archive
from tests.test_appgenerator_revision_baseline import baseline as revision_baseline  # noqa: F401
from tests.test_runtime_database_migrations import FakeCursor, FakeDatabase, FakeMongoClient

PACK = Path(__file__).resolve().parents[1] / "factory_app/build_context/messaging"
MIGRATION = "data/migrations/001_messaging_collections.json"


def _context(root=PACK, *, pinned=True):
    metadata = verify_pack_integrity(root, "messaging")
    selected = {"id": "messaging", "pack_source_path": str(root), "capability_source": "generated_module"}
    if pinned:
        selected.update({key: metadata[key] for key in ("version", "digest")})
    return {"capability_packs": [selected], "app_build_plan": {"capability_packs": [{
        "capability_pack_id": "messages", "capability_source": "generated_module",
        "primary_entities": ["Thread", "Message", "ThreadRead"],
    }]}}


def _assembled_files():
    context = _context()
    emitted = resolve_managed_capability_templates(context["capability_packs"], context_variables=context)
    return {file["filename"]: file["content"] for file in _merge_code_files(
        [{"code_files": emitted}], app_build_plan=context["app_build_plan"],
        data_contract={"version": "1", "surfaces": [], "shared_collections": []}, context_variables=context,
    )}


def test_messaging_compiles_exact_backend_and_admitted_data_without_new_public_crud():
    files = _assembled_files()
    assert validate_module_implementation_contract(files)["passed"] is True
    assert audit_module_runtime_quality([{"filename": path, "content": content} for path, content in files.items()]) == []
    contract = json.loads(files["data/contract.json"])
    assert contract["surfaces"][0]["surface_id"] == "messages"
    assert {row["name"] for row in contract["surfaces"][0]["collections"]} == {"threads", "messages", "thread_reads"}
    assert all(row["tenancy"] == "app_wide" for row in contract["surfaces"][0]["collections"])
    for path in (PACK / "templates/modules/messages").rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            assert files[path.relative_to(PACK / "templates").as_posix()] == path.read_text(encoding="utf-8")
    actions = {row["id"] for row in yaml.safe_load(files["modules/messages/module.yaml"])["actions"]}
    assert "list_threads" in actions
    assert not actions & {"get_threads", "list_messages", "get_messages", "list_thread_reads", "get_thread_reads",
                          "create_threads", "update_threads", "delete_threads", "create_messages", "delete_messages"}
    provenance = json.loads(files[".mozaiks/pack_provenance.json"])
    assert provenance["composed_outputs"]["data/contract.json"][0]["pack_id"] == "messaging"
    assert _scan_pack_provenance_manifest(files) == []


@pytest.mark.asyncio
async def test_original_approved_context_reaches_assembly_and_acceptance_scanner_without_precompilation():
    from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
    from mozaiksai.core.workflow.context.adapter import create_context_container
    from mozaiksai.core.workflow.context.frozen import detach
    from tests.factory_context import factory_context

    approved = {"version": "1", "surfaces": [], "shared_collections": []}
    original = deepcopy(approved)
    context = create_context_container(initial=factory_context({
        **_context(), "app_id": "ordinary_messaging", "data_contract": approved,
        "generated_files": {
            "app.json": json.dumps({"appId": "ordinary_messaging", "appName": "Messages", "authRequired": True}),
            "data/contract.json": json.dumps(approved),
        },
    }))
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    files = {file["filename"]: file["content"] for file in result["code_files"]}
    assert approved == original
    assert detach(context.get("data_contract")) == original
    assert [surface["surface_id"] for surface in json.loads(files["data/contract.json"])["surfaces"]] == ["messages"]
    plan_modules = detach(context.get("app_build_plan"))["capability_packs"]
    assert all("pack_source_path" not in module for module in plan_modules)
    assert scan_generated_bundle(
        files, capability_packs=plan_modules, planned_data_contract=approved, context_variables=context,
    ) == []
    acceptance = await run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert acceptance["bundle_scan"]["passed"], acceptance["bundle_scan"]
    assert detach(context.get("data_contract")) == original


@pytest.mark.parametrize("change", ["missing_collection", "changed_field", "extra_field", "undeclared_output"])
def test_normal_context_scanner_keeps_exact_pack_contract_and_output_boundaries(change):
    context = _context()
    files = _assembled_files()
    contract = json.loads(files["data/contract.json"])
    collections = contract["surfaces"][0]["collections"]
    if change == "missing_collection":
        collections.pop()
    elif change == "changed_field":
        collections[0]["fields"][0]["type"] = "integer"
    elif change == "extra_field":
        collections[0]["fields"].append({"name": "injected", "type": "string", "required": False})
    else:
        files["ui/components/Undeclared.jsx"] = "export default function Undeclared() { return null; }"
    files["data/contract.json"] = json.dumps(contract)
    errors = scan_generated_bundle(
        files, capability_packs=context["app_build_plan"]["capability_packs"],
        planned_data_contract={"version": "1", "surfaces": [], "shared_collections": []},
        context_variables=context,
    )
    expected = "outside the canonical app planes" if change == "undeclared_output" else "must match the compiled approved"
    assert any(expected in error for error in errors), errors


@pytest.mark.parametrize("key,value", [("version", "0.0.0"), ("digest", "sha256:" + "0" * 64)])
def test_normal_context_scanner_reverifies_selected_pack_pins(key, value):
    context = _context()
    files = _assembled_files()
    context["capability_packs"][0][key] = value
    errors = scan_generated_bundle(
        files, capability_packs=context["app_build_plan"]["capability_packs"],
        planned_data_contract={"version": "1", "surfaces": [], "shared_collections": []},
        context_variables=context,
    )
    assert any(f"selected {key} does not match freshly verified source" in error for error in errors), errors


@pytest.mark.parametrize("change", ["unknown_pack", "wrong_digest", "absolute_path", "duplicate", "exclusive"])
def test_composed_provenance_rejects_invalid_contributor_claims(change):
    files = _assembled_files()
    provenance = json.loads(files[".mozaiks/pack_provenance.json"])
    contributor = provenance["composed_outputs"]["data/contract.json"][0]
    if change == "unknown_pack":
        contributor["pack_id"] = "unselected"
    elif change == "wrong_digest":
        contributor["digest"] = "sha256:" + "0" * 64
    elif change == "absolute_path":
        contributor["source_path"] = "C:/private/pack"
    elif change == "duplicate":
        provenance["composed_outputs"]["data/contract.json"].append(dict(contributor))
    else:
        provenance["packs"][0]["materialized_owned_files"].append({"path": "data/contract.json", "owner": "templates"})
    files[".mozaiks/pack_provenance.json"] = json.dumps(provenance)
    assert _scan_pack_provenance_manifest(files)


def test_data_contribution_is_idempotent_and_preserves_other_collections():
    contribution = json.loads((PACK / "templates/data/contract.json").read_text())
    unrelated = {"version": "1", "app_id": "preserved", "surfaces": [{
        "surface_id": "projects", "surface_kind": "module", "collections": [{
            "name": "projects", "entity": "Project", "scope": "app", "tenancy": "per_user",
            "owner_field": "owner", "fields": [{"name": "owner", "type": "string", "required": True}],
        }],
    }], "shared_collections": [], "policies": {"retention": "preserved"}}
    original = deepcopy(unrelated)
    compiled = compile_data_contract(unrelated, pack_data_contracts=[contribution])
    assert unrelated == original
    assert compiled["surfaces"][0] == unrelated["surfaces"][0]
    assert compiled["app_id"] == "preserved" and compiled["policies"] == unrelated["policies"]
    assert compile_data_contract(compiled, pack_data_contracts=[contribution]) == compiled
    conflict = deepcopy(contribution)
    conflict["surfaces"][0]["collections"][0]["owner_field"] = "injected_owner"
    with pytest.raises(ValueError):
        compile_data_contract(compiled, pack_data_contracts=[conflict])


def test_data_contribution_order_is_deterministic_and_duplicate_owners_fail():
    contribution = json.loads((PACK / "templates/data/contract.json").read_text())
    other = deepcopy(contribution)
    other["surfaces"][0]["surface_id"] = "another_module"
    base = {"version": "1", "surfaces": [{"surface_id": "z_app", "surface_kind": "module", "collections": []}]}
    first = compile_data_contract(base, pack_data_contracts=[contribution, other])
    assert first == compile_data_contract(base, pack_data_contracts=[other, contribution])
    assert [surface["surface_id"] for surface in first["surfaces"]] == ["z_app", "another_module", "messages"]
    duplicate = deepcopy(contribution)
    duplicate["surfaces"] *= 2
    with pytest.raises(ValueError, match="duplicates|duplicate surface"):
        compile_data_contract(base, pack_data_contracts=[duplicate])


@pytest.mark.parametrize("repaired", ["nothing", "migration"])
def test_persistence_repair_save_keeps_the_verified_pack_data_contributions(repaired):
    from factory_app.workflows.AppGenerator.tools.code_file_utils import (
        admitted_app_file_map,
        save_generated_code,
    )
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        _scan_planned_data_fields,
    )
    from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair

    approved = {"version": "1", "surfaces": [{
        "surface_id": "projects", "surface_kind": "module", "collections": [{
            "name": "projects", "entity": "Project", "scope": "app", "tenancy": "per_user",
            "owner_field": "owner", "fields": [{"name": "owner", "type": "string", "required": True}],
        }],
    }], "shared_collections": []}
    migration = "data/migrations/001_projects.json"
    task = {
        "task_id": "data_contract", "task_type": "persistence_contract", "capability_pack_id": None,
        "surface_id": "data_contract", "surface_kind": "module", "initial_agent": "DatabaseAgent",
        "execution_target": "AppGenerator", "description": "Data contract", "initial_message": "Data contract",
        "owned_paths": ["data/contract.json", migration], "depends_on": [],
    }
    context = _context()
    context["app_build_plan"] = {**context["app_build_plan"], "build_tasks": [task]}
    templates = resolve_managed_capability_templates(context["capability_packs"], context_variables=context)
    assembled = {file["filename"]: file["content"] for file in _merge_code_files(
        [{"code_files": templates}], app_build_plan=context["app_build_plan"], data_contract=approved,
        context_variables={**context, "data_contract": approved},
    )}
    context.update({
        "data_contract": approved, "generated_files": assembled,
        "app_task_batch_results": {"data_contract": {"code_files": []}},
    })
    assert _scan_planned_data_fields(assembled, approved, context_variables=context) == []
    # Acceptance routes a diagnostic to the task that owns data/contract.json.
    repair = prepare_bundle_repair({"passed": False, "errors": [f"{migration}: missing projects collection"]}, context)
    assert (repair["target_agent"], repair["active"]["allowed_paths"]) == ("DatabaseAgent", task["owned_paths"])
    fixed = {"migration_id": "001_projects", "operations": [
        {"type": "ensure_collection", "module_id": "projects", "entity_name": "Project"},
    ]}
    context["structured_output"] = {"code_files": [
        {"filename": migration, "content": json.dumps(fixed)},
    ] if repaired == "migration" else []}
    saved = save_generated_code(context)
    assert saved.get("status") != "rejected", saved
    assert migration in saved["saved_files"] if repaired == "migration" else migration not in saved["saved_files"]
    # The owner's save compiles the contract as assembly does, with the pack's surfaces.
    admitted = admitted_app_file_map(context)
    assert [surface["surface_id"] for surface in json.loads(admitted["data/contract.json"])["surfaces"]] == [
        "projects", "messages",
    ]
    assert admitted["data/contract.json"] == assembled["data/contract.json"]
    assert _scan_planned_data_fields(admitted, approved, context_variables=context) == []


CARRIED_APP_CONTRACT = {"version": "1", "surfaces": [{
    "surface_id": "projects", "surface_kind": "module", "collections": [{
        "name": "projects", "entity": "Project", "scope": "app", "tenancy": "per_user",
        "owner_field": "owner", "fields": [{"name": "owner", "type": "string", "required": True}],
    }],
}], "shared_collections": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("with_pack", [False, True])
async def test_revision_without_approved_contract_never_replaces_the_carried_app_contract(request, with_pack):
    from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
    from mozaiksai.core.workflow.context.adapter import create_context_container

    # An imported app: its bundle carries its own contract, but DesignDocs never
    # approved one, so the run has no data_contract. A data-contributing pack
    # must not turn the missing approval into a pack-only contract.
    saved = request.getfixturevalue("revision_baseline")
    saved.files = {
        "app.json": json.dumps({"appId": "customer-app", "appName": "Projects"}),
        "data/contract.json": json.dumps(CARRIED_APP_CONTRACT),
    }
    _replace_archive(saved, {})
    context = create_context_container(initial={**saved.context, **(_context() if with_pack else {})})
    result = await assemble_app_tasks(context_variables=context)
    assert not result["success"], [file["filename"] for file in result["code_files"]]
    assert "data/contract.json requires the approved DesignDocs data_contract" in result["error"]


def _imported_revision_without_an_approved_contract(request):
    """An imported app's revision: its bundle carries its own contract, but DesignDocs approved none.

    The plan's persistence task owns data/contract.json, so the guard's
    diagnostic is dispatched to it as a bounded repair.
    """
    from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
    from mozaiksai.core.workflow.context.adapter import create_context_container

    saved = request.getfixturevalue("revision_baseline")
    saved.files = {
        "app.json": json.dumps({"appId": "customer-app", "appName": "Projects"}),
        "data/contract.json": json.dumps(CARRIED_APP_CONTRACT),
    }
    _replace_archive(saved, {})
    task = {
        "task_id": "data_contract", "task_type": "persistence_contract", "capability_pack_id": None,
        "surface_id": "data_contract", "surface_kind": "module", "initial_agent": "DatabaseAgent",
        "execution_target": "AppGenerator", "description": "Data contract", "initial_message": "Data contract",
        "owned_paths": ["data/contract.json"], "depends_on": [],
    }
    selected = _context()
    selected["app_build_plan"] = {**selected["app_build_plan"], "build_tasks": [task]}
    context = create_context_container(initial={
        **saved.context, **selected, "app_task_batch_results": {"data_contract": {"code_files": []}},
    })
    return context, selected, assemble_app_tasks


@pytest.mark.asyncio
async def test_the_carried_contract_guard_refuses_the_repair_save_its_diagnostic_dispatches(request):
    """The data/contract.json owner's save would write the packs' contributions on an empty base; it is refused."""
    from factory_app.workflows.AppGenerator.tools.app_validation import _assembly_failure
    from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code

    context, _, assemble_app_tasks = _imported_revision_without_an_approved_contract(request)
    first = await assemble_app_tasks(context_variables=context)
    assert not first["success"] and "holds app-owned data: surface 'projects'" in first["error"], first
    repair = _assembly_failure(context, prepare_recovery=True)["bundle_repair"]
    assert (repair["target_agent"], repair["active"]["allowed_paths"]) == ("DatabaseAgent", ["data/contract.json"])
    context.set("structured_output", {"code_files": []})
    refused = save_generated_code(context)
    assert refused.get("status") == "rejected", refused
    assert "holds app-owned data: surface 'projects'" in refused["error"]
    assert "data/contract.json" not in {item["filename"] for item in context.get("code_files") or []}


@pytest.mark.asyncio
@pytest.mark.parametrize("copy_over_the_carried_contract", ["repair_overlay", "task_output"])
async def test_a_copy_over_the_carried_contract_cannot_drop_the_apps_own_data(request, copy_over_the_carried_contract):
    """Assembly checks every copy of data/contract.json the bundle carries, not the one that wins the merge.

    A pack-only copy written over the carried contract, by a repair's overlay
    or a task's output, fails reassembly with the guard's reason, and the
    bundle keeps the carried contract.
    """
    context, selected, assemble_app_tasks = _imported_revision_without_an_approved_contract(request)
    first = await assemble_app_tasks(context_variables=context)
    assert not first["success"] and "holds app-owned data: surface 'projects'" in first["error"], first
    pack_only = {file["filename"]: file["content"] for file in _merge_code_files(
        [{"code_files": resolve_managed_capability_templates(selected["capability_packs"], context_variables=selected)}],
        app_build_plan=selected["app_build_plan"], data_contract=None, context_variables=selected,
    )}["data/contract.json"]
    assert [surface["surface_id"] for surface in json.loads(pack_only)["surfaces"]] == ["messages"]
    copy = [{"filename": "data/contract.json", "content": pack_only}]
    if copy_over_the_carried_contract == "repair_overlay":
        context.set("code_files", copy)
    else:
        context.set("app_task_batch_results", {"data_contract": {"code_files": copy}})
    again = await assemble_app_tasks(context_variables=context)
    assert not again["success"], [surface["surface_id"] for surface in json.loads(
        {file["filename"]: file["content"] for file in again["code_files"]}["data/contract.json"],
    )["surfaces"]]
    assert "holds app-owned data: surface 'projects'" in again["error"]
    # The failed assembly keeps the bundle it had; nothing published drops the app's surface.
    published = (context.get("generated_files") or {}).get("data/contract.json")
    assert published is None or "projects" in [surface["surface_id"] for surface in json.loads(published)["surfaces"]]


def test_pack_only_contract_without_approval_reassembles_only_its_own_composition():
    context = _context()
    emitted = resolve_managed_capability_templates(context["capability_packs"], context_variables=context)

    def merge(carried):
        return {file["filename"]: file["content"] for file in _merge_code_files(
            [{"code_files": [*emitted, *carried]}], app_build_plan=context["app_build_plan"],
            data_contract=None, context_variables=context,
        )}

    written = merge([])["data/contract.json"]
    assert [surface["surface_id"] for surface in json.loads(written)["surfaces"]] == ["messages"]
    # A reassembly of this build carries the composition it wrote: nothing to drop.
    assert merge([{"filename": "data/contract.json", "content": written}])["data/contract.json"] == written
    extended = json.loads(written)
    extended["surfaces"].extend(CARRIED_APP_CONTRACT["surfaces"])
    for carried in (json.dumps(extended), "not json"):
        with pytest.raises(ValueError, match="requires the approved DesignDocs data_contract"):
            merge([{"filename": "data/contract.json", "content": carried}])


def _assignment_store_subscription():
    config = {
        "schema_version": "mozaiks.subscriptions.v1", "label": "Plans", "default_plan_id": "free",
        "assignment_store": {"data_alias": "billing.subscriptions", "user_id_field": "user_id",
                             "active_statuses": ["active"]},
        "plans": [
            {"plan_id": "free", "label": "Free", "capabilities": []},
            {"plan_id": "pro", "label": "Pro", "capabilities": []},
        ],
    }
    return {"contract_required": True, "subscription_config_file": config,
            "selected_features_by_plan": {"free": [], "pro": []}, "module_contract_updates": []}


def _pack_only_merge():
    context = _context()
    emitted = resolve_managed_capability_templates(context["capability_packs"], context_variables=context)

    def merge(contract=None, subscription=None):
        carried_files = [] if contract is None else [{"filename": "data/contract.json", "content": contract}]
        return json.loads({file["filename"]: file["content"] for file in _merge_code_files(
            [{"code_files": [*emitted, *carried_files]}], app_build_plan=context["app_build_plan"],
            data_contract=None, subscription_contract=subscription, context_variables=context,
        )}["data/contract.json"])
    return context, merge


@pytest.mark.parametrize("carried", [
    "lifecycle_reordered", "gains_assignment_store", "pack_contribution_changed", "app_surface", "app_shared_collection",
    "app_surface_with_the_pack_surface_id", "app_collection_and_alias_in_pack_surface",
    "app_collection_in_pack_surface", "app_collection_in_assignment_surface", "app_alias_of_a_pack_collection",
    "pack_surface_of_another_kind", "assignment_surface_of_another_kind",
])
def test_pack_only_contract_replaces_a_carried_contract_only_when_it_holds_no_app_data(tmp_path, carried):
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        _recompose_data_contract,
    )

    context, merge = _pack_only_merge()
    subscription = _assignment_store_subscription()
    plain, with_store = merge(), merge(subscription=subscription)
    assert [surface["surface_id"] for surface in with_store["surfaces"]] == ["messages", "subscription_assignments"]
    if carried == "lifecycle_reordered":
        # The pack lifecycle recomposes the same contract with its surfaces in another order.
        (tmp_path / "data").mkdir()
        (tmp_path / "data/contract.json").write_text(json.dumps(with_store), encoding="utf-8")
        contributions = verified_pack_data_contracts(verified_pack_template_inventory(context_variables=context))
        text, conflict = _recompose_data_contract(tmp_path, contributions, contributions)
        assert conflict is None and json.loads(text)["surfaces"] == with_store["surfaces"][::-1]
        assert merge(text, subscription) == with_store
    elif carried == "gains_assignment_store":
        # A pack-only app whose revision adds a subscription assignment store.
        assert merge(json.dumps(plain), subscription) == with_store
    elif carried == "pack_contribution_changed":
        # The carried composition came from an earlier version of the pack's contribution.
        earlier = deepcopy(plain)
        earlier["surfaces"][0]["collections"][0]["fields"].pop()
        assert merge(json.dumps(earlier)) == plain
    else:
        app_data = deepcopy(with_store)
        app_collection = deepcopy(CARRIED_APP_CONTRACT["surfaces"][0]["collections"][0])
        surfaces = {surface["surface_id"]: surface for surface in app_data["surfaces"]}
        if carried == "app_surface":
            app_data["surfaces"].extend(CARRIED_APP_CONTRACT["surfaces"])
            owned = ["surface 'projects'"]
        elif carried == "app_shared_collection":
            app_data["shared_collections"] = [{"name": "notes", "entity": "Note", "scope": "app", "fields": []}]
            owned = ["shared collections"]
        elif carried == "app_surface_with_the_pack_surface_id":
            # An imported app's own surface that happens to share the pack's surface id.
            app_data = {**deepcopy(CARRIED_APP_CONTRACT), "surfaces": [{
                "surface_id": "messages", "surface_kind": "module",
                "collections": [{**app_collection, "name": "inbox_items", "entity": "InboxItem"}],
            }]}
            owned = ["collection 'inbox_items' in surface 'messages'"]
        elif carried == "app_collection_and_alias_in_pack_surface":
            surfaces["messages"]["collections"].append(app_collection)
            app_data["aliases"].append({"alias": "app.projects", "collection": "projects"})
            owned = ["collection 'projects' in surface 'messages'", "alias 'app.projects' -> 'projects'"]
        elif carried == "app_collection_in_pack_surface":
            # No alias names it: the collection itself is what the composition does not declare.
            surfaces["messages"]["collections"].append(app_collection)
            owned = ["collection 'projects' in surface 'messages'"]
        elif carried == "app_collection_in_assignment_surface":
            surfaces["subscription_assignments"]["collections"].append(app_collection)
            owned = ["collection 'projects' in surface 'subscription_assignments'"]
        elif carried == "pack_surface_of_another_kind":
            # The pack's surface id and collections under a surface_kind the composition does not declare.
            surfaces["messages"]["surface_kind"] = "app_policy"
            owned = ["surface 'messages' of kind 'app_policy', composed as 'module'"]
        elif carried == "assignment_surface_of_another_kind":
            surfaces["subscription_assignments"]["surface_kind"] = "module"
            owned = ["surface 'subscription_assignments' of kind 'module', composed as 'app_policy'"]
        else:
            # An app alias naming a pack collection is the app's own, too.
            app_data["aliases"].append({"alias": "app.inbox", "collection": "messages"})
            owned = ["alias 'app.inbox' -> 'messages'"]
        # Reordering never hides app data: the app's own surface would be dropped.
        app_data["surfaces"].reverse()
        with pytest.raises(ValueError, match="requires the approved DesignDocs data_contract") as raised:
            merge(json.dumps(app_data), subscription)
        message = str(raised.value)
        assert "holds app-owned data: " in message and "no longer matches" not in message, message
        assert all(detail in message for detail in owned), (owned, message)


@pytest.mark.parametrize("carried", [
    "pack_collection_stored_elsewhere", "assignment_store_no_longer_compiled", "assignment_alias_renamed",
    "pack_surface_without_kind",
])
def test_carried_derived_data_whose_identity_changed_fails_naming_that_change(carried):
    # Nothing here is the app's own, but a derived collection no longer matches the composition.
    # Replacing it would orphan its stored rows, so assembly fails and names what changed.
    _, merge = _pack_only_merge()
    subscription = _assignment_store_subscription()
    current, earlier = subscription, merge(subscription=subscription)
    if carried == "pack_collection_stored_elsewhere":
        earlier["surfaces"][0]["collections"][0]["mongo_collection"] = "threads_v1"
        changed = ["derived collection 'threads' in surface 'messages' is stored in 'threads_v1', composed as 'threads'"]
    elif carried == "assignment_store_no_longer_compiled":
        current = None
        changed = [
            "subscription assignment store 'billing_subscriptions' in surface 'subscription_assignments' is not "
            "one the current subscription contract compiles",
            "subscription assignment alias 'billing.subscriptions' -> 'billing_subscriptions' is not one",
        ]
    elif carried == "assignment_alias_renamed":
        renamed = _assignment_store_subscription()
        renamed["subscription_config_file"]["assignment_store"]["data_alias"] = "billing.plans"
        earlier = merge(subscription=renamed)
        changed = [
            "subscription assignment store 'billing_plans' in surface 'subscription_assignments' is not one",
            "subscription assignment alias 'billing.plans' -> 'billing_plans' is not one",
        ]
    else:
        earlier["surfaces"][0].pop("surface_kind")
        changed = ["surface 'messages' has no surface_kind, composed as 'module'"]
    with pytest.raises(ValueError, match="requires the approved DesignDocs data_contract") as raised:
        merge(json.dumps(earlier), current)
    message = str(raised.value)
    assert "the carried contract's derived data no longer matches the current pack/assignment composition" in message
    assert all(detail in message for detail in changed), (changed, message)
    assert "app-owned" not in message
    assert message.endswith("Reassemble with an approved DesignDocs data_contract or migrate the stored data")


@pytest.mark.parametrize("path", ["modules/messages/backend/policy.py", "modules/messages/backend/service.py",
                                  "modules/messages/module.yaml"])
def test_injected_pack_backend_bytes_are_rejected(path):
    context = _context()
    inventory = verified_pack_template_inventory(context_variables=context)
    files = _assembled_files()
    files[path] += "\n# injected\n"
    with pytest.raises(PackIntegrityError, match="template bytes differ"):
        verified_template_module_ids(files, inventory)


def test_workspace_handler_extension_is_preserved_but_missing_or_extra_helpers_fail():
    inventory = verified_pack_template_inventory(context_variables=_context())
    files = _assembled_files()
    files["modules/messages/backend/handler.py"] += "\n# workspace extension\n"
    assert verified_template_module_ids(files, inventory) == {"messages"}
    for path, operation in (("modules/messages/backend/injected.py", "add"),
                            ("modules/messages/backend/policy.py", "remove")):
        altered = dict(files)
        if operation == "add":
            altered[path] = "# extra helper"
        else:
            altered.pop(path)
        with pytest.raises(PackIntegrityError, match="complete declared template closure"):
            verified_template_module_ids(altered, inventory)


def test_complete_template_implementation_requires_a_declared_handler():
    inventory = verified_pack_template_inventory(context_variables=_context())
    handler = "modules/messages/backend/handler.py"
    inventory[0]["owner_map"].pop(handler)
    inventory[0]["files"] = [file for file in inventory[0]["files"] if file["filename"] != handler]
    files = _assembled_files()
    files.pop(handler)
    with pytest.raises(PackIntegrityError, match="requires a declared handler template"):
        verified_template_module_ids(files, inventory)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_admitted_handler", [True, False])
async def test_actual_assembly_preserves_admitted_workspace_handler_and_restores_templates(has_admitted_handler):
    from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
    from tests.factory_context import factory_context

    class Context(dict):
        def set(self, key, value):
            self[key] = value

    handler = "modules/messages/backend/handler.py"
    policy = "modules/messages/backend/policy.py"
    admitted_handler = (PACK / "templates" / handler).read_text(encoding="utf-8") + "\n# App-owned handler extension retained across assembly.\n"
    contract = {"version": "1", "surfaces": [], "shared_collections": []}
    context = Context(factory_context({
        **_context(), "app_id": "workspace_handler_test",
        "data_contract": contract,
        "generated_files": {**({handler: admitted_handler} if has_admitted_handler else {}),
                            policy: "# injected policy must be discarded\n",
                            "data/contract.json": json.dumps(contract)},
        "app_task_batch_results": {"untrusted_worker": {"code_files": [
            {"filename": handler, "content": "# raw task copy must not replace admitted handler\n"},
            {"filename": policy, "content": "# raw task copy must not replace template policy\n"},
        ]}},
    }))
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    files = {file["filename"]: file["content"] for file in result["code_files"]}
    expected_handler = admitted_handler if has_admitted_handler else (PACK / "templates" / handler).read_text(encoding="utf-8")
    assert files[handler] == expected_handler
    assert files[policy] == (PACK / "templates" / policy).read_text()
    assert validate_module_implementation_contract(files)["passed"] is True


@pytest.mark.asyncio
async def test_actual_revision_assembly_retains_verified_archive_workspace_handler(request):
    from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
    from mozaiksai.core.workflow.context.adapter import create_context_container

    saved_baseline = request.getfixturevalue("revision_baseline")
    handler = "modules/messages/backend/handler.py"
    policy = "modules/messages/backend/policy.py"
    saved_baseline.files = {**_assembled_files(), "app.json": json.dumps({
        "appId": "customer-app", "appName": "Messaging", "authRequired": True,
    })}
    saved_baseline.files[handler] += "\n    workspace_marker = 'retained from verified archive'\n"
    _replace_archive(saved_baseline, {})
    context = create_context_container(initial={
        **saved_baseline.context, **_context(),
        "data_contract": json.loads(saved_baseline.files["data/contract.json"]),
        "app_task_batch_results": {"old_worker": {"code_files": [
            {"filename": handler, "content": "# raw task copy must not replace admitted handler\n"},
            {"filename": policy, "content": "# raw task copy must not replace template policy\n"},
        ]}},
    })
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    files = {file["filename"]: file["content"] for file in result["code_files"]}
    assert files[handler] == saved_baseline.files[handler]
    assert files[policy] == saved_baseline.files[policy]
    assert validate_module_implementation_contract(files)["passed"] is True
    saved_baseline.content.get_bundle.assert_awaited()


def test_unverified_external_source_cannot_opt_out_of_compilation(tmp_path):
    root = tmp_path / "messaging"
    shutil.copytree(PACK, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    context = _context(root, pinned=False)
    inventory = verified_pack_template_inventory(context_variables=context)
    with pytest.raises(PackIntegrityError, match="verified source pin"):
        verified_template_module_ids(_assembled_files(), inventory)


@pytest.mark.parametrize("key,value", [("version", "0.0.0"), ("digest", "sha256:" + "0" * 64)])
def test_stale_selected_pins_are_rejected(key, value):
    context = _context()
    context["capability_packs"][0][key] = value
    with pytest.raises(PackIntegrityError, match=f"selected {key}"):
        resolve_managed_capability_templates(context["capability_packs"], context_variables=context)


@pytest.mark.parametrize("pinned", [True, False])
def test_unresolvable_factory_root_trusts_only_pinned_packs(monkeypatch, pinned):
    from factory_app.workflows.AppGenerator.tools import (
        resolve_managed_capability_templates as resolver,
    )

    monkeypatch.setattr(resolver, "resolve_factory_app_root", lambda: None)
    context = _context(pinned=pinned)
    [entry] = resolver.verified_selected_pack_sources(context_variables=context)
    assert entry["implementation_trusted"] is pinned


def test_mutated_source_after_selection_fails_fresh_digest_check(tmp_path):
    root = tmp_path / "messaging"
    shutil.copytree(PACK, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    context = _context(root)
    (root / "templates/modules/messages/backend/policy.py").write_text("# replaced policy\n")
    with pytest.raises(PackIntegrityError, match="selected digest"):
        resolve_managed_capability_templates(context["capability_packs"], context_variables=context)


def test_data_cannot_claim_an_unshipped_module_and_workspace_cannot_own_helpers():
    inventory = verified_pack_template_inventory(context_variables=_context())
    invalid = deepcopy(inventory)
    invalid[0]["owner_map"].pop("modules/messages/module.yaml")
    with pytest.raises(PackIntegrityError, match="undeclared module owner"):
        verified_pack_data_contracts(invalid)
    invalid = deepcopy(inventory)
    invalid[0]["owner_map"]["modules/messages/backend/base_handler.py"] = "workspace"
    with pytest.raises(PackIntegrityError, match="workspace ownership"):
        verified_template_module_ids(_assembled_files(), invalid)


@pytest.mark.asyncio
async def test_actual_messaging_migration_applies_once_and_preserves_existing_records(tmp_path):
    files = _assembled_files()
    target = tmp_path / MIGRATION
    target.parent.mkdir(parents=True)
    target.write_text(files[MIGRATION], encoding="utf-8")
    migrations = load_data_migrations(tmp_path)
    client = FakeMongoClient()
    persistence = MongoPersistenceContext(app_id="messaging_test", client=client,
                                          data_contract=json.loads(files["data/contract.json"]))
    names = [persistence.collection_name("messages", name) for name in ("threads", "messages", "thread_reads")]
    preserved = {("seed", "row"): {"text": "existing conversation"}}
    for name in [*names, "unrelated_projects"]:
        client[DEFAULT_APP_DATABASE_NAME][name].document_by_key = deepcopy(preserved)
    first = await apply_data_migrations(app_id="messaging_test", migrations=migrations,
                                       persistence=persistence, history_client=client)
    index_counts = [len(client[DEFAULT_APP_DATABASE_NAME][name].create_index_calls) for name in names]
    second = await apply_data_migrations(app_id="messaging_test", migrations=migrations,
                                        persistence=persistence, history_client=client)
    assert (first, second) == (1, 0)
    assert index_counts == [6, 3, 2]
    assert [len(client[DEFAULT_APP_DATABASE_NAME][name].create_index_calls) for name in names] == index_counts
    assert all(client[DEFAULT_APP_DATABASE_NAME][name].document_by_key == preserved for name in [*names, "unrelated_projects"])


ACCOUNT_APP_ID = "messaging_account_test"
ACCOUNT_DATABASE = "messaging_account_test_db"
# The neutral title a surviving thread keeps once its creator's account is deleted.
RELEASED_TITLE = "Conversation"
THREAD_EXPORT_FIELDS = frozenset({
    "thread_id", "thread_type", "title", "status", "scope_type", "scope_id", "subject_app_id",
    "related_type", "related_id", "created_at",
})
_MISSING = object()


def _field(row, path):
    value = row
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _equal(actual, expected):
    if actual is _MISSING:
        return expected is None
    if isinstance(actual, list) and not isinstance(expected, list):
        return expected in actual
    return actual == expected


def _operator(actual, name, operand):
    if name == "$ne":
        return not _equal(actual, operand)
    if name == "$gt":
        return actual is not _MISSING and actual > operand
    raise NotImplementedError(f"fake Mongo does not model {name}")


def _matches(row, query):
    for key, expected in query.items():
        if key == "$and":
            matched = all(_matches(row, part) for part in expected)
        elif key.startswith("$"):
            raise NotImplementedError(f"fake Mongo does not model {key}")
        elif isinstance(expected, dict) and any(name.startswith("$") for name in expected):
            matched = all(_operator(_field(row, key), name, operand) for name, operand in expected.items())
        else:
            matched = _equal(_field(row, key), expected)
        if not matched:
            return False
    return True


def _equalities(query):
    fields = {}
    for key, value in query.items():
        if key == "$and":
            for part in value:
                fields.update(_equalities(part))
        elif not (isinstance(value, dict) and any(name.startswith("$") for name in value)):
            fields[key] = value
    return fields


class _DocumentCollection:
    """Motor-shaped rows with ObjectId keys; an unmodelled operator fails the test."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def _matching(self, query):
        return [row for row in self.rows if _matches(row, query)]

    def find(self, query, projection=None, **_options):
        return FakeCursor(deepcopy(self._matching(query)))

    async def find_one(self, query, projection=None, **_options):
        return next(iter(deepcopy(self._matching(query))), None)

    async def insert_one(self, document, **_options):
        row = {"_id": ObjectId(), **deepcopy(dict(document))}
        self.rows.append(row)
        return SimpleNamespace(inserted_id=row["_id"])

    async def update_one(self, query, update, *, upsert=False, **_options):
        operators = ("$set", "$pull", "$unset", "$addToSet")
        changes, pulls, unsets, additions = (update.get(name) or {} for name in operators)
        if (set(update) - set(operators) or any("." in key for key in [*changes, *pulls, *unsets, *additions])
                or any(isinstance(value, dict) for value in [*pulls.values(), *additions.values()])
                or (upsert and (pulls or unsets or additions))):
            raise NotImplementedError(f"fake Mongo does not model update {update}")
        rows = self._matching(query)[:1]
        for row in rows:
            row.update(deepcopy(changes))
            for key, value in pulls.items():
                if isinstance(row.get(key), list):
                    row[key] = [item for item in row[key] if item != value]
            for key, value in additions.items():
                if not isinstance(row.setdefault(key, []), list):
                    raise NotImplementedError(f"fake Mongo does not model $addToSet on {key}")
                if value not in row[key]:
                    row[key].append(value)
            for key in unsets:
                row.pop(key, None)
        if not rows and upsert:
            await self.insert_one({**_equalities(query), **changes})
        return SimpleNamespace(matched_count=len(rows), modified_count=len(rows))

    async def _delete(self, query, limit=None):
        doomed = self._matching(query)[:limit]
        self.rows = [row for row in self.rows if not any(row is target for target in doomed)]
        return SimpleNamespace(deleted_count=len(doomed))

    async def delete_one(self, query, **_options):
        return await self._delete(query, limit=1)

    async def delete_many(self, query, **_options):
        return await self._delete(query)

    async def count_documents(self, query, **_options):
        return len(self._matching(query))


class _DocumentDatabase(FakeDatabase):
    def __getitem__(self, name):
        return self.collections.setdefault(name, _DocumentCollection())


class _DocumentMongoClient(FakeMongoClient):
    def __getitem__(self, name):
        return self.databases.setdefault(name, _DocumentDatabase())


@pytest.fixture
def messaging_account(tmp_path, monkeypatch):
    """Load the rendered pack the way a host does and bind its account handler to fake Mongo."""
    files = _assembled_files()
    for filename, content in files.items():
        if filename.startswith("modules/messages/"):
            path = tmp_path / "app" / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    registry = AccountDataRegistry()
    monkeypatch.setattr("mozaiksai.core.account.account_data_registry", registry)
    loaded = ModuleLoader(str(tmp_path / "app")).load("messages")
    assert registry.registered_module_ids() == ["messages"]
    client = _DocumentMongoClient()
    contract = json.loads(files["data/contract.json"])

    def persistence(user_id):
        # Module dispatch and the account routes both bind user_id, which stamps inserted rows.
        return MongoPersistenceContext(
            app_id=ACCOUNT_APP_ID, user_id=user_id, database_name=ACCOUNT_DATABASE, client=client,
            data_contract=contract, principal=PersistencePrincipal(user_id=user_id),
        )

    def collection(name):
        return client[ACCOUNT_DATABASE][persistence("probe").collection_name("messages", name)]

    def rows(name):
        return collection(name).rows

    def snapshot():
        return deepcopy({name: rows(name) for name in ("threads", "messages", "thread_reads")})

    async def account(operation, user_id, *, app_id=ACCOUNT_APP_ID):
        # The account routes call the registry with the requesting account's persistence.
        call = registry.delete_all if operation == "delete" else registry.export_all
        return await call(app_id=app_id, user_id=user_id, db=None, persistence=persistence(user_id))

    def ctx(user_id):
        return SimpleNamespace(user_id=user_id, app_id=ACCOUNT_APP_ID, persistence=persistence(user_id))

    module = loaded.handler

    async def thread(user_id, peer):
        result = await module.create_thread(ctx(user_id), participant_ids=[peer], thread_type="direct")
        return result["thread"]["thread_id"]

    async def send(user_id, thread_id, body):
        result = await module.send_message(ctx(user_id), thread_id=thread_id, body=body)
        assert result["success"], result
        return result["message"]

    async def stored_thread(user_id, thread_id):
        return (await module.get_thread(ctx(user_id), thread_id=thread_id))["thread"]

    async def group(user_id, peers):
        result = await module.create_thread(ctx(user_id), participant_ids=peers, thread_type="group", title="Trip")
        return result["thread"]["thread_id"]

    def stored_row(thread_id):
        return next(row for row in rows("threads") if row["thread_id"] == thread_id)

    return SimpleNamespace(module=module, ctx=ctx, persistence=persistence, collection=collection, rows=rows,
                           snapshot=snapshot, account=account, thread=thread, group=group, send=send,
                           stored_thread=stored_thread, stored_row=stored_row)


@pytest.mark.asyncio
async def test_messaging_account_deletion_preserves_peers_and_recomputes_previews(messaging_account):
    rt = messaging_account
    secret = "my home address is 12 Elm St"
    # Alice spoke last in Bob's thread: Bob's own message (longer than a preview) becomes the preview again.
    bob_thread = await rt.thread("bob", "alice")
    bob_message = await rt.send("bob", bob_thread, "b" * 200)
    bob_preview = (await rt.stored_thread("bob", bob_thread))["last_message"]
    await rt.send("alice", bob_thread, secret)
    for user_id in ("alice", "bob"):
        await rt.module.mark_thread_read(rt.ctx(user_id), thread_id=bob_thread)
    # Only Alice spoke in Bob's thread; a retracted Bob row must not resurface as the preview.
    silent_thread = await rt.thread("bob", "alice")
    await rt.persistence("bob").collection("messages", "messages").insert_one({
        "message_id": "msg_retracted", "thread_id": silent_thread, "sender_id": "bob", "sender_role": "user",
        "body": "retracted", "created_at": "2000-01-01T00:00:00+00:00", "is_deleted": True,
    })
    await rt.send("alice", silent_thread, secret)
    # Alice's thread with a peer reply survives with the reply as its preview.
    shared_thread = await rt.thread("alice", "bob")
    await rt.send("bob", shared_thread, "bob reply")
    reply_preview = (await rt.stored_thread("bob", shared_thread))["last_message"]
    await rt.send("alice", shared_thread, secret)
    # Alice's thread without peer messages is hers alone and is deleted.
    alice_thread = await rt.thread("alice", "bob")
    await rt.send("alice", alice_thread, secret)
    # A thread whose latest message is Bob's keeps its row exactly.
    peer_last_thread = await rt.thread("bob", "alice")
    await rt.send("alice", peer_last_thread, secret)
    await rt.send("bob", peer_last_thread, "bob has the last word")
    peer_last_row = await rt.stored_thread("bob", peer_last_thread)

    before = rt.snapshot()
    assert await rt.account("delete", "alice", app_id="foreign_app") == {
        "messages": {"error": "account persistence is bound to another app"},
    }
    assert rt.snapshot() == before

    assert await rt.account("delete", "alice") == {
        "messages": {"threads_deleted": 1, "messages_deleted": 5, "memberships_removed": 4, "read_state_deleted": 1},
    }
    threads = {row["thread_id"]: row for row in rt.rows("threads")}
    assert set(threads) == {bob_thread, silent_thread, shared_thread, peer_last_thread}
    assert threads[bob_thread]["last_message"] == bob_preview
    assert threads[bob_thread]["last_message_at"] == bob_message["created_at"]
    assert (threads[silent_thread]["last_message"], threads[silent_thread]["last_message_at"]) == (None, None)
    assert threads[shared_thread]["last_message"] == reply_preview
    # Surviving peer conversations drop Alice's membership and her creator identity.
    assert {thread_id: row["participant_ids"] for thread_id, row in threads.items()} == dict.fromkeys(threads, ["bob"])
    assert (threads[shared_thread]["created_by"], "user_id" in threads[shared_thread]) == ("", False)
    assert await rt.stored_thread("bob", peer_last_thread) == {**peer_last_row, "participant_ids": ["bob"]}
    assert {row["sender_id"] for row in rt.rows("messages")} == {"bob"}
    assert [row["user_id"] for row in rt.rows("thread_reads")] == ["bob"]
    assert secret not in json.dumps(rt.snapshot(), default=str)
    assert "alice" not in json.dumps(rt.snapshot(), default=str)
    assert secret not in json.dumps(await rt.module.list_threads(rt.ctx("bob")), default=str)
    bob_view = await rt.module.get_thread(rt.ctx("bob"), thread_id=bob_thread)
    assert [row["message_id"] for row in bob_view["messages"]] == [bob_message["message_id"]]

    after = rt.snapshot()
    assert await rt.account("delete", "alice") == {
        "messages": {"threads_deleted": 0, "messages_deleted": 0, "memberships_removed": 0, "read_state_deleted": 0},
    }
    assert rt.snapshot() == after


@pytest.mark.asyncio
async def test_messaging_account_deletion_repoints_to_the_newest_remaining_peer_message(messaging_account):
    rt = messaging_account
    group = await rt.group("bob", ["carol", "alice"])
    await rt.send("bob", group, "bob older")
    carol_message = await rt.send("carol", group, "carol newest")
    # Stored after Carol's message but sent first: recency follows created_at, not storage order.
    await rt.persistence("bob").collection("messages", "messages").insert_one({
        "message_id": "msg_backfilled", "thread_id": group, "sender_id": "bob", "sender_role": "user",
        "body": "bob backfilled", "created_at": "2000-01-01T00:00:00+00:00", "is_deleted": False,
    })
    await rt.send("alice", group, "alice spoke last")

    await rt.account("delete", "alice")
    row = rt.stored_row(group)
    assert row["last_message"] == {
        "message_id": carol_message["message_id"], "sender_id": "carol", "sender_role": "user",
        "body_preview": "carol newest", "sent_at": carol_message["created_at"],
    }
    assert row["last_message_at"] == carol_message["created_at"]


def _is_repoint_query(query, user_id):
    text = json.dumps(query, default=str)
    return '"is_deleted"' in text and json.dumps({"$ne": user_id})[1:-1] in text


@pytest.mark.asyncio
async def test_messaging_account_deletion_keeps_a_preview_a_peer_sent_meanwhile(messaging_account, monkeypatch):
    rt = messaging_account
    thread_id = await rt.thread("bob", "alice")
    await rt.send("bob", thread_id, "bob earlier")
    await rt.send("alice", thread_id, "alice spoke last")
    messages = rt.collection("messages")
    find, raced = messages.find, []

    def find_then_peer_sends(query, projection=None, **options):
        cursor = find(query, projection, **options)
        if not raced and _is_repoint_query(query, "alice"):
            stale_to_list = cursor.to_list

            async def to_list(length=None):
                # The handler now holds a stale newest peer row; Bob's send commits before its write.
                rows = await stale_to_list(length)
                raced.append(await rt.send("bob", thread_id, "bob meanwhile"))
                return rows

            cursor.to_list = to_list
        return cursor

    monkeypatch.setattr(messages, "find", find_then_peer_sends)
    await rt.account("delete", "alice")
    (meanwhile,) = raced
    row = rt.stored_row(thread_id)
    assert (row["last_message"]["message_id"], row["last_message"]["body_preview"]) == (
        meanwhile["message_id"], "bob meanwhile",
    )
    assert row["last_message_at"] == meanwhile["created_at"]


@pytest.mark.asyncio
async def test_messaging_account_deletion_releases_membership_and_keeps_peer_access(messaging_account):
    rt = messaging_account
    group = await rt.group("alice", ["bob", "carol"])
    await rt.send("bob", group, "bob joins")
    await rt.send("alice", group, "alice plans")
    for user_id in ("alice", "carol"):
        await rt.module.mark_thread_read(rt.ctx(user_id), thread_id=group)
    carol_read = deepcopy([row for row in rt.rows("thread_reads") if row["user_id"] == "carol"])

    assert await rt.account("delete", "alice") == {
        "messages": {"threads_deleted": 0, "messages_deleted": 1, "memberships_removed": 1, "read_state_deleted": 1},
    }
    row = rt.stored_row(group)
    assert (row["participant_ids"], row["created_by"], row["title"]) == (["bob", "carol"], "", RELEASED_TITLE)
    assert rt.rows("thread_reads") == carol_read
    assert "alice" not in json.dumps(rt.snapshot(), default=str)

    # The remaining participants keep the conversation, and Alice receives no new notifications.
    events = []
    bob = SimpleNamespace(**vars(rt.ctx("bob")), emit=lambda _event_type, payload: events.append(payload))
    assert [thread["thread_id"] for thread in (await rt.module.list_threads(rt.ctx("carol")))["threads"]] == [group]
    assert (await rt.module.send_message(bob, thread_id=group, body="still here"))["success"]
    assert [event["recipient_ids"] for event in events] == [["carol"]]
    assert await rt.module.mark_thread_read(rt.ctx("carol"), thread_id=group) == {"success": True}

    # The deleted account's id is no longer admitted to the conversation it left.
    alice = rt.ctx("alice")
    assert (await rt.module.list_threads(alice))["threads"] == []
    assert (await rt.module.get_thread(alice, thread_id=group))["error"] == "access denied"
    assert (await rt.module.send_message(alice, thread_id=group, body="back again"))["error"] == "access denied"
    assert (await rt.module.mark_thread_read(alice, thread_id=group))["success"] is False

    # No later account request reclaims the released thread, including through the blank marker.
    exported = await rt.account("export", "alice")
    assert (exported["threads_created"], exported["thread_memberships"]) == ([], [])
    before = rt.snapshot()
    for blank in ("", " "):
        assert await rt.account("delete", blank) == {"messages": {"error": "account user_id is required"}}
        assert await rt.account("export", blank) == {"messages.__error__": "account user_id is required"}
    assert rt.snapshot() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", ["bob", "operator"])
async def test_messaging_send_overlapping_account_deletion_cannot_restore_the_member(
    messaging_account, monkeypatch, sender,
):
    """A send reads the thread, the deletion releases Alice, then the send writes: Alice stays released.

    Bob is a participant; the operator is admitted by the send itself, as a support reply admits it.
    """
    rt = messaging_account
    group = await rt.group("bob", ["alice", "carol"])
    await rt.send("alice", group, "alice was here")
    threads = rt.collection("threads")
    find_one, deleted = threads.find_one, []

    async def read_then_delete_alice(query, projection=None, **options):
        row = await find_one(query, projection, **options)
        if row is not None and not deleted:
            # The send now holds a copy that still lists Alice; her deletion commits before its write.
            assert "alice" in row["participant_ids"]
            deleted.append(await rt.account("delete", "alice"))
        return row

    monkeypatch.setattr(threads, "find_one", read_then_delete_alice)
    sent = await rt.module._service.send_message(
        rt.ctx(sender), thread_id=group, body=f"{sender} meanwhile", allow_nonparticipant_sender=sender == "operator",
    )
    assert sent["success"], sent
    assert deleted == [{"messages": {
        "threads_deleted": 0, "messages_deleted": 1, "memberships_removed": 1, "read_state_deleted": 0,
    }}]
    row = rt.stored_row(group)
    assert row["participant_ids"] == ["bob", "carol", *(["operator"] if sender == "operator" else [])]
    # The send still records its own preview.
    assert (row["last_message"]["message_id"], row["last_message_at"]) == (
        sent["message"]["message_id"], sent["message"]["created_at"],
    )
    assert "alice" not in json.dumps(rt.snapshot(), default=str)

    # Alice is not admitted again, and later sends do not address her.
    alice = rt.ctx("alice")
    assert (await rt.module.list_threads(alice))["threads"] == []
    assert (await rt.module.get_thread(alice, thread_id=group))["error"] == "access denied"
    assert (await rt.account("export", "alice"))["thread_memberships"] == []
    events = []
    carol = SimpleNamespace(**vars(rt.ctx("carol")), emit=lambda _event_type, payload: events.append(payload))
    assert (await rt.module.send_message(carol, thread_id=group, body="carol after"))["success"]
    assert [sorted(event["recipient_ids"]) for event in events] == [
        ["bob", *(["operator"] if sender == "operator" else [])],
    ]


@pytest.mark.asyncio
async def test_messaging_account_deletion_anonymises_titles_of_threads_the_user_created(messaging_account):
    rt = messaging_account
    service = rt.module._service
    secret = "alice private support text about her medical bill"
    # The support pack titles a support thread with the opening message (create_support_request).
    support = (await service.create_thread(
        rt.ctx("alice"), title=secret[:80], participant_ids=[], thread_type="support",
        related_type="support.request", related_id="sr_1",
    ))["thread"]["thread_id"]
    assert (await service.send_message(
        rt.ctx("alice"), thread_id=support, body=secret, allow_support_thread_sender=True,
    ))["success"]
    reply = await service.send_message(
        rt.ctx("operator"), thread_id=support, body="operator reply", sender_role="operator",
        allow_nonparticipant_sender=True, allow_support_thread_sender=True,
    )
    assert reply["success"]
    assert rt.stored_row(support)["participant_ids"] == ["alice", "operator"]
    group = await rt.group("alice", ["bob"])
    await rt.send("bob", group, "bob joins")
    direct = await rt.thread("alice", "bob")
    await rt.send("bob", direct, "bob says hi")
    bob_group = (await rt.module.create_thread(
        rt.ctx("bob"), participant_ids=["alice"], thread_type="group", title="Bob's plans",
    ))["thread"]["thread_id"]
    await rt.send("alice", bob_group, "alice answers")

    await rt.account("delete", "alice")
    titles = {thread_id: rt.stored_row(thread_id)["title"] for thread_id in (support, group, direct, bob_group)}
    # Titles Alice wrote become the neutral title; an untitled thread stays untitled; peers' titles stay.
    assert titles == {support: RELEASED_TITLE, group: RELEASED_TITLE, direct: "", bob_group: "Bob's plans"}
    assert secret[:20] not in json.dumps(rt.snapshot(), default=str)
    assert "alice" not in json.dumps(rt.snapshot(), default=str)

    # The remaining participants keep their access to the anonymised threads.
    operator_view = await service.get_thread(rt.ctx("operator"), thread_id=support, allow_support_thread_reader=True)
    assert (operator_view["thread"]["title"], [row["body"] for row in operator_view["messages"]]) == (
        RELEASED_TITLE, ["operator reply"],
    )
    bob_view = await rt.module.get_thread(rt.ctx("bob"), thread_id=group)
    assert (bob_view["thread"]["title"], [row["body"] for row in bob_view["messages"]]) == (
        RELEASED_TITLE, ["bob joins"],
    )
    assert (await rt.module.send_message(rt.ctx("bob"), thread_id=group, body="still here"))["success"]
    after = rt.snapshot()
    await rt.account("delete", "alice")
    assert rt.snapshot() == after


@pytest.mark.asyncio
async def test_messaging_account_deletion_clears_annotations_of_threads_the_user_created(messaging_account):
    rt = messaging_account
    secret = "my home address is 12 Elm St"
    thread_id = (await rt.module.create_thread(
        rt.ctx("alice"), participant_ids=["bob"], thread_type="direct",
        metadata=[{"key": "topic", "value": secret}],
    ))["thread"]["thread_id"]
    await rt.send("bob", thread_id, "bob replies")
    await rt.send("alice", thread_id, "alice answers")
    # Annotations a peer wrote on a thread Alice joined belong to that peer.
    bob_thread = (await rt.module.create_thread(
        rt.ctx("bob"), participant_ids=["alice"], thread_type="group", title="Bob's plans",
        metadata=[{"key": "topic", "value": "bob launch plan"}],
    ))["thread"]["thread_id"]
    await rt.send("alice", bob_thread, "alice joins")

    await rt.account("delete", "alice")
    # Bob keeps the thread Alice created, without the annotations she wrote.
    assert rt.stored_row(thread_id)["metadata"] == {}
    bob_view = await rt.module.get_thread(rt.ctx("bob"), thread_id=thread_id)
    assert (bob_view["thread"]["metadata"], [row["body"] for row in bob_view["messages"]]) == ({}, ["bob replies"])
    assert rt.stored_row(bob_thread)["metadata"] == {"topic": "bob launch plan"}
    assert secret not in json.dumps(await rt.module.list_threads(rt.ctx("bob")), default=str)
    assert secret not in json.dumps(rt.snapshot(), default=str)
    assert "alice" not in json.dumps(rt.snapshot(), default=str)
    after = rt.snapshot()
    await rt.account("delete", "alice")
    assert rt.snapshot() == after


@pytest.mark.asyncio
async def test_messaging_account_export_of_created_threads_holds_only_the_users_own_data(messaging_account):
    rt = messaging_account
    peer_text = "bob private reply"
    thread_id = await rt.thread("alice", "bob")
    await rt.send("alice", thread_id, "alice opens")
    await rt.send("bob", thread_id, peer_text)

    exported = await rt.account("export", "alice")
    (created,) = exported["threads_created"]
    # The membership projection plus the creator's own thread annotations: no
    # peer preview, participant ids, or storage metadata.
    assert set(created) == THREAD_EXPORT_FIELDS | {"metadata"}
    assert created["thread_id"] == thread_id
    membership = next(row for row in exported["thread_memberships"] if row["thread_id"] == thread_id)
    assert {key: value for key, value in created.items() if key != "metadata"} == membership
    assert peer_text not in json.dumps(exported["threads_created"])
    assert "bob" not in json.dumps(exported["threads_created"])


@pytest.mark.asyncio
async def test_messaging_account_export_keeps_the_creators_thread_annotations(messaging_account):
    rt = messaging_account
    created = await rt.module.create_thread(
        rt.ctx("alice"), participant_ids=["bob"], thread_type="direct",
        metadata=[{"key": "topic", "value": "alice launch plan"}],
    )
    thread_id = created["thread"]["thread_id"]

    exported = await rt.account("export", "alice")
    (row,) = exported["threads_created"]
    assert row["thread_id"] == thread_id and row["metadata"] == {"topic": "alice launch plan"}
    # Another participant's export never carries the creator's annotations.
    (membership,) = (await rt.account("export", "bob"))["thread_memberships"]
    assert membership["thread_id"] == thread_id and "metadata" not in membership


@pytest.mark.asyncio
async def test_messaging_account_export_leaves_titles_peers_wrote_out_of_membership_rows(messaging_account):
    rt = messaging_account
    service = rt.module._service
    secret = "alice private support text about her medical bill"
    # The support pack titles a support thread with the opening message, and an operator reply admits the operator.
    support = (await service.create_thread(
        rt.ctx("alice"), title=secret[:80], participant_ids=[], thread_type="support",
        related_type="support.request", related_id="sr_1",
    ))["thread"]["thread_id"]
    assert (await service.send_message(
        rt.ctx("alice"), thread_id=support, body=secret, allow_support_thread_sender=True,
    ))["success"]
    assert (await service.send_message(
        rt.ctx("operator"), thread_id=support, body="operator reply", sender_role="operator",
        allow_nonparticipant_sender=True, allow_support_thread_sender=True,
    ))["success"]
    assert rt.stored_row(support)["participant_ids"] == ["alice", "operator"]
    group_title = "alice private group name"
    group = (await rt.module.create_thread(
        rt.ctx("alice"), participant_ids=["bob"], thread_type="group", title=group_title,
    ))["thread"]["thread_id"]

    for user_id, thread_id in (("operator", support), ("bob", group)):
        exported = await rt.account("export", user_id)
        # The member keeps the membership facts, but not the title Alice wrote.
        stored = rt.stored_row(thread_id)
        assert exported["thread_memberships"] == [
            {field: stored[field] for field in THREAD_EXPORT_FIELDS - {"title"}},
        ]
        assert exported["threads_created"] == []
        assert secret[:20] not in json.dumps(exported) and group_title not in json.dumps(exported)

    # Alice's own export keeps the titles she wrote.
    exported = await rt.account("export", "alice")
    for rows in (exported["threads_created"], exported["thread_memberships"]):
        assert {row["thread_id"]: row["title"] for row in rows} == {support: secret[:80], group: group_title}


@pytest.mark.asyncio
async def test_messaging_account_export_is_json_safe_and_covers_deleted_collections(messaging_account):
    rt = messaging_account
    peer_text = "bob's private last word"
    bob_thread = await rt.thread("bob", "alice")
    await rt.send("bob", bob_thread, "hello alice")
    alice_message = await rt.send("alice", bob_thread, "hello bob")
    await rt.send("bob", bob_thread, peer_text)
    alice_thread = await rt.thread("alice", "bob")
    for user_id in ("alice", "bob"):
        await rt.module.mark_thread_read(rt.ctx(user_id), thread_id=bob_thread)
    reads = rt.persistence("alice").collection("messages", "thread_reads")
    for index in range(150):  # Read state spanning more than one export page.
        await reads.insert_one({"thread_id": f"thr_page_{index}", "user_id": "alice", "read_at": "2026-01-01T00:00:00+00:00"})
    carol_threads = rt.persistence("carol").collection("messages", "threads")
    for index in range(100):  # Memberships in threads Alice did not create, spanning more than one page.
        await carol_threads.insert_one({
            "thread_id": f"thr_member_{index}", "thread_type": "group", "participant_ids": ["carol", "alice"],
            "created_by": "carol", "last_message": {"sender_id": "carol", "body_preview": peer_text},
        })

    assert await rt.account("export", "alice", app_id="foreign_app") == {
        "messages.__error__": "account persistence is bound to another app",
    }
    exported = await rt.account("export", "alice")
    assert json.loads(json.dumps(exported)) == exported  # Storage ObjectIds never reach account JSON.
    assert set(exported) == {"threads_created", "messages_sent", "thread_reads", "thread_memberships"}
    assert [row["thread_id"] for row in exported["threads_created"]] == [alice_thread]
    assert [row["message_id"] for row in exported["messages_sent"]] == [alice_message["message_id"]]
    assert {row["user_id"] for row in exported["thread_reads"]} == {"alice"}
    assert len({row["thread_id"] for row in exported["thread_reads"]}) == len(exported["thread_reads"]) == 151
    memberships = exported["thread_memberships"]
    assert len({row["thread_id"] for row in memberships}) == len(memberships) == 102
    assert {row["thread_id"] for row in memberships} >= {bob_thread, alice_thread, "thr_member_0", "thr_member_99"}
    # Membership is a fixed projection: peers' previews, ids, titles, and storage metadata stay out.
    # Only a thread Alice created carries its title, which she wrote.
    assert {row["thread_id"]: frozenset(row) for row in memberships} == {
        row["thread_id"]: THREAD_EXPORT_FIELDS if row["thread_id"] == alice_thread else THREAD_EXPORT_FIELDS - {"title"}
        for row in memberships
    }
    assert not {peer_text, "bob", "carol"} & {value for row in memberships for value in row.values()}
    assert peer_text not in json.dumps(memberships)

    deleted = (await rt.account("delete", "alice"))["messages"]
    assert deleted["messages_deleted"] == len(exported["messages_sent"])
    assert deleted["read_state_deleted"] == len(exported["thread_reads"])
    assert deleted["threads_deleted"] + deleted["memberships_removed"] == len(memberships)
    assert [row["user_id"] for row in rt.rows("thread_reads")] == ["bob"]


@pytest.mark.skipif(os.getenv("MOZAIKS_RUN_MESSAGING_MONGO_TEST") != "1", reason="opt-in task-owned local Mongo")
@pytest.mark.asyncio
async def test_messaging_real_mongo_fresh_repeat_and_account_peer_preservation():
    from motor.motor_asyncio import AsyncIOMotorClient

    from mozaiksai.core.runtime.persistence import APP_DATA_MIGRATIONS_COLLECTION, migration_hash

    uri = os.environ["MOZAIKS_MESSAGING_TEST_MONGO_URI"]
    assert urlparse(uri).hostname in {"127.0.0.1", "localhost"}, "test requires task-owned local Mongo"
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database_name = f"messaging_pack_test_{uuid4().hex}"
    app_id = f"messaging_test_{uuid4().hex}"
    files = _assembled_files()
    migration = json.loads(files[MIGRATION])
    persistence = MongoPersistenceContext(
        app_id=app_id, database_name=database_name, client=client,
        data_contract=json.loads(files["data/contract.json"]), principal=PersistencePrincipal(user_id="alice"),
    )
    database = client[database_name]
    history = client["mozaiksai"][APP_DATA_MIGRATIONS_COLLECTION]
    names = {name: persistence.collection_name("messages", name) for name in ("threads", "messages", "thread_reads")}
    try:
        await client.admin.command("ping")
        await database["unrelated_projects"].insert_one({"_id": "project", "title": "preserve unrelated bytes"})
        assert await apply_data_migrations(app_id=app_id, migrations=[migration], persistence=persistence,
                                           history_client=client) == 1
        for name, expected_count in (("threads", 6), ("messages", 3), ("thread_reads", 2)):
            indexes = await database[names[name]].list_indexes().to_list(length=100)
            assert len(indexes) == expected_count + 1  # Mongo's _id index.
        threads = persistence.collection("messages", "threads")
        messages = persistence.collection("messages", "messages")
        reads = persistence.collection("messages", "thread_reads")
        for thread_id in ("shared", "solo"):
            await threads.insert_one({"thread_id": thread_id, "created_by": "alice", "participant_ids": ["alice", "bob"]})
        for message_id, thread_id, sender in (("alice_shared", "shared", "alice"), ("bob_shared", "shared", "bob"),
                                               ("alice_solo", "solo", "alice")):
            await messages.insert_one({"message_id": message_id, "thread_id": thread_id, "sender_id": sender, "body": message_id})
        await reads.insert_one({"thread_id": "shared", "user_id": "alice", "scope_type": "app", "scope_id": app_id})
        bob_persistence = MongoPersistenceContext(
            app_id=app_id, database_name=database_name, client=client,
            data_contract=json.loads(files["data/contract.json"]), principal=PersistencePrincipal(user_id="bob"),
        )
        await bob_persistence.collection("messages", "thread_reads").insert_one({
            "thread_id": "shared", "user_id": "bob", "scope_type": "app", "scope_id": app_id,
        })
        before = {name: await database[name].find({}).sort("_id", 1).to_list(length=100)
                  for name in [*names.values(), "unrelated_projects"]}
        assert await apply_data_migrations(app_id=app_id, migrations=[migration], persistence=persistence,
                                           history_client=client) == 0
        after = {name: await database[name].find({}).sort("_id", 1).to_list(length=100) for name in before}
        assert after == before
        receipt = await history.find_one({"app_id": app_id, "migration_id": migration["migration_id"]})
        assert receipt["status"] == "applied" and receipt["migration_hash"] == migration_hash(migration)
        namespace = {}
        exec(compile(files["modules/messages/backend/account_data_handler.py"], "account_data_handler.py", "exec"), namespace)
        handler = namespace["AccountDataHandler"](persistence)
        exported = await handler.export_user_data(app_id=app_id, user_id="alice")
        assert {row["message_id"] for row in exported["messages_sent"]} == {"alice_shared", "alice_solo"}
        assert [row["user_id"] for row in exported["thread_reads"]] == ["alice"]
        assert sorted(row["thread_id"] for row in exported["thread_memberships"]) == ["shared", "solo"]
        json.dumps(exported)  # Mongo _id values must not escape into account JSON.
        with pytest.raises(PermissionError, match="another app"):
            await handler.export_user_data(app_id="foreign", user_id="alice")
        assert await handler.delete_user_data(app_id=app_id, user_id="alice") == {
            "threads_deleted": 1, "messages_deleted": 2, "memberships_removed": 1, "read_state_deleted": 1,
        }
        assert [(row["thread_id"], row["created_by"], row["participant_ids"])
                for row in await threads.find_many({})] == [("shared", "", ["bob"])]
        assert [row["message_id"] for row in await messages.find_many({})] == ["bob_shared"]
        assert [row["user_id"] for row in await reads.find_many({})] == ["bob"]
        assert await database["unrelated_projects"].find_one({"_id": "project"}) == before["unrelated_projects"][0]
    finally:
        await history.delete_many({"app_id": app_id})
        await client.drop_database(database_name)
        client.close()
