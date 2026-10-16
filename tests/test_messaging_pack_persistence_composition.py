"""Verified template persistence joins the admitted app without generic CRUD."""
from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
import yaml

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
from mozaiksai.core.runtime.persistence import apply_data_migrations, load_data_migrations
from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal
from mozaiksai.core.runtime.persistence.mongo import (
    DEFAULT_APP_DATABASE_NAME,
    MongoPersistenceContext,
)
from mozaiksai.core.workflow.generator_support.code_files import compile_data_contract
from tests.test_appgenerator_revision_baseline import _replace_archive
from tests.test_appgenerator_revision_baseline import baseline as revision_baseline  # noqa: F401
from tests.test_runtime_database_migrations import FakeMongoClient

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
    from factory_app.workflows.AppGenerator.tools import resolve_managed_capability_templates as resolver

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
        json.dumps(exported)  # Mongo _id values must not escape into account JSON.
        with pytest.raises(PermissionError, match="another app"):
            await handler.export_user_data(app_id="foreign", user_id="alice")
        assert await handler.delete_user_data(app_id=app_id, user_id="alice") == {
            "threads_deleted": 1, "messages_deleted": 2, "read_state_deleted": 1,
        }
        assert [row["thread_id"] for row in await threads.find_many({})] == ["shared"]
        assert [row["message_id"] for row in await messages.find_many({})] == ["bob_shared"]
        assert [row["user_id"] for row in await reads.find_many({})] == ["bob"]
        assert await database["unrelated_projects"].find_one({"_id": "project"}) == before["unrelated_projects"][0]
    finally:
        await history.delete_many({"app_id": app_id})
        await client.drop_database(database_name)
        client.close()
