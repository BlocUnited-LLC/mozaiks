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
        changes = update.get("$set") or {}
        if set(update) - {"$set"} or any("." in key for key in changes):
            raise NotImplementedError(f"fake Mongo does not model update {update}")
        rows = self._matching(query)[:1]
        for row in rows:
            row.update(deepcopy(changes))
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
        return MongoPersistenceContext(
            app_id=ACCOUNT_APP_ID, database_name=ACCOUNT_DATABASE, client=client,
            data_contract=contract, principal=PersistencePrincipal(user_id=user_id),
        )

    def rows(name):
        return client[ACCOUNT_DATABASE][persistence("probe").collection_name("messages", name)].rows

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

    return SimpleNamespace(module=module, ctx=ctx, persistence=persistence, rows=rows, snapshot=snapshot,
                           account=account, thread=thread, send=send, stored_thread=stored_thread)


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
        "messages": {"threads_deleted": 1, "messages_deleted": 5, "read_state_deleted": 1},
    }
    threads = {row["thread_id"]: row for row in rt.rows("threads")}
    assert set(threads) == {bob_thread, silent_thread, shared_thread, peer_last_thread}
    assert threads[bob_thread]["last_message"] == bob_preview
    assert threads[bob_thread]["last_message_at"] == bob_message["created_at"]
    assert (threads[silent_thread]["last_message"], threads[silent_thread]["last_message_at"]) == (None, None)
    assert threads[shared_thread]["last_message"] == reply_preview
    # Surviving peer conversations keep the opaque ids (see the handler's deletion policy).
    assert (threads[shared_thread]["created_by"], threads[shared_thread]["participant_ids"]) == ("alice", ["alice", "bob"])
    assert await rt.stored_thread("bob", peer_last_thread) == peer_last_row
    assert {row["sender_id"] for row in rt.rows("messages")} == {"bob"}
    assert [row["user_id"] for row in rt.rows("thread_reads")] == ["bob"]
    assert secret not in json.dumps(rt.snapshot(), default=str)
    assert secret not in json.dumps(await rt.module.list_threads(rt.ctx("bob")), default=str)
    bob_view = await rt.module.get_thread(rt.ctx("bob"), thread_id=bob_thread)
    assert [row["message_id"] for row in bob_view["messages"]] == [bob_message["message_id"]]

    after = rt.snapshot()
    assert await rt.account("delete", "alice") == {
        "messages": {"threads_deleted": 0, "messages_deleted": 0, "read_state_deleted": 0},
    }
    assert rt.snapshot() == after


@pytest.mark.asyncio
async def test_messaging_account_export_is_json_safe_and_covers_deleted_collections(messaging_account):
    rt = messaging_account
    bob_thread = await rt.thread("bob", "alice")
    await rt.send("bob", bob_thread, "hello alice")
    alice_message = await rt.send("alice", bob_thread, "hello bob")
    alice_thread = await rt.thread("alice", "bob")
    for user_id in ("alice", "bob"):
        await rt.module.mark_thread_read(rt.ctx(user_id), thread_id=bob_thread)
    reads = rt.persistence("alice").collection("messages", "thread_reads")
    for index in range(150):  # Read state spanning more than one export page.
        await reads.insert_one({"thread_id": f"thr_page_{index}", "user_id": "alice", "read_at": "2026-01-01T00:00:00+00:00"})

    assert await rt.account("export", "alice", app_id="foreign_app") == {
        "messages.__error__": "account persistence is bound to another app",
    }
    exported = await rt.account("export", "alice")
    assert json.loads(json.dumps(exported)) == exported  # Storage ObjectIds never reach account JSON.
    assert set(exported) == {"threads_created", "messages_sent", "thread_reads"}
    assert [row["thread_id"] for row in exported["threads_created"]] == [alice_thread]
    assert [row["message_id"] for row in exported["messages_sent"]] == [alice_message["message_id"]]
    assert {row["user_id"] for row in exported["thread_reads"]} == {"alice"}
    assert len({row["thread_id"] for row in exported["thread_reads"]}) == len(exported["thread_reads"]) == 151

    deleted = (await rt.account("delete", "alice"))["messages"]
    assert deleted["messages_deleted"] == len(exported["messages_sent"])
    assert deleted["read_state_deleted"] == len(exported["thread_reads"])
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
