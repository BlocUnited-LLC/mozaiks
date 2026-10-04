"""Platform modules mounted into a workspace persist only through their own declarations.

Studio mounts ``factory_app/app/modules`` into every workspace. Each mounted
module is bound by the collections ``factory_app/app/data/contract.json``
declares for it, beside the workspace's own contract, and the allow-list stays
strict for app and platform modules alike (issue #790).
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.runtime.app.loader import AppLoader, AppLoadError
from mozaiksai.core.runtime.composition.module_authority import ModuleDispatchAuthority
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.persistence import (
    DataContractLoadError,
    MongoPersistenceContext,
    PersistencePrincipal,
    PersistenceScopeError,
    PlatformModuleDeclarations,
)
from mozaiksai.core.runtime.persistence.alias_collection import GuardedAliasCollection
from mozaiksai.core.runtime.persistence.intent_loader import (
    load_data_contract,
    validate_complete_data_contract_ownership,
)
from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceCollection
from mozaiksai.core.runtime.persistence.ownership import CollectionOwnership
from mozaiksai.resources import resolve_factory_app_root
from tests.test_runtime_persistence_mongo import FakeMongoClient

FACTORY = resolve_factory_app_root() / "app"
STUDIO_MODULES = {
    "app_registry", "factory_control_plane", "messages", "security_readiness",
    "user_onboarding", "workspace_integrations", "workspace_support",
}
STUDIO_COLLECTIONS = {
    ("messages", "messages"), ("messages", "thread_reads"), ("messages", "threads"),
    ("security_readiness", "findings"), ("user_onboarding", "status"),
    ("workspace_integrations", "integration_notes"),
    ("workspace_support", "feedback"), ("workspace_support", "requests"),
}
# The placeholder `mozaiks init` writes: a present contract, so an allow-list.
SCAFFOLD_CONTRACT = {"enabled": False, "version": "1", "surfaces": [], "shared_collections": []}


@pytest.fixture(autouse=True)
def restore_module_imports(monkeypatch):
    from mozaiksai.core.account.registry import account_data_registry

    monkeypatch.setattr(account_data_registry, "_handlers", {})
    paths = list(sys.path)
    modules = dict(sys.modules)
    yield
    sys.path[:] = paths
    for key in list(sys.modules):
        if key in {"services", "modules"} or key.startswith(("services.", "modules.", "mozaiks_runtime_module_")):
            if key in modules:
                sys.modules[key] = modules[key]
            else:
                sys.modules.pop(key, None)


# --------------------------------------------------------------------------- composition rules


def platform_contract() -> dict:
    return {"version": "1", "surfaces": [
        {"surface_id": "support", "surface_kind": "module", "collections": [
            {"name": "requests", "entity": "Request", "tenancy": "app_wide", "owner_field": None},
        ]},
        {"surface_id": "inbox", "surface_kind": "module", "collections": [
            {"name": "threads", "entity": "Thread", "tenancy": "app_wide", "owner_field": None},
            {"name": "drafts", "entity": "Draft", "tenancy": "app_wide", "owner_field": None},
        ]},
        {"surface_id": "tour", "surface_kind": "module", "collections": [
            {"name": "status", "entity": "TourStatus", "tenancy": "per_user", "owner_field": "user_id"},
        ]},
    ], "shared_collections": [
        {"owner_module": "inbox", "name": "threads", "shared_with": [{"module": "support"}]},
    ]}


def workspace_contract(*, tenancy: str = "per_user", **extra) -> dict:
    return {"version": "1", "surfaces": [
        {"surface_id": "tasks", "surface_kind": "module", "collections": [{
            "name": "tasks", "entity": "Task", "tenancy": tenancy,
            "owner_field": "user_id" if tenancy == "per_user" else None,
        }]},
    ], "shared_collections": [], **extra}


def mounted(*module_ids: str) -> PlatformModuleDeclarations:
    return PlatformModuleDeclarations(platform_contract(), module_ids or ("support", "inbox", "tour"))


def context(module_id: str, *, workspace=None, platform=None, principal=None) -> MongoPersistenceContext:
    return MongoPersistenceContext(
        app_id="app-a", client=FakeMongoClient(), data_contract=workspace,
        platform_modules=platform if platform is not None else mounted(), module_id=module_id,
        principal=principal,
    )


def refused(ctx: MongoPersistenceContext, module_id: str, name: str) -> bool:
    try:
        ctx.collection(module_id, name)
    except PersistenceScopeError:
        return True
    return False


def test_app_module_keeps_the_workspace_allow_list_and_cannot_reach_platform_collections():
    ctx = context("tasks", workspace=workspace_contract())
    assert not refused(ctx, "tasks", "tasks")
    assert refused(ctx, "tasks", "undeclared")
    assert refused(ctx, "support", "requests")
    assert refused(ctx, "inbox", "threads")


def test_app_module_in_a_contractless_workspace_still_cannot_reach_platform_collections():
    ctx = context("tasks", workspace=None)
    assert not refused(ctx, "tasks", "anything")
    assert ctx.literal_collection("ownerless_records") is not None
    for module_id, name in (("support", "requests"), ("SUPPORT", "Requests"), ("tour", "status")):
        assert refused(ctx, module_id, name), (module_id, name)
    with pytest.raises(PersistenceScopeError):
        ctx.literal_collection(context("support").collection_name("support", "requests"))


def test_platform_module_reaches_only_the_collections_declared_for_it():
    ctx = context("support", workspace=workspace_contract())
    assert not refused(ctx, "support", "requests")
    assert not refused(ctx, "support", "Request")  # its declared entity
    assert not refused(ctx, "inbox", "threads")  # shared with it by its owner
    assert refused(ctx, "inbox", "drafts")  # another platform module's, not shared
    assert refused(ctx, "tour", "status")
    assert refused(ctx, "tasks", "tasks")  # the app's
    assert refused(ctx, "support", "undeclared")
    assert ctx.literal_collection(ctx.collection_name("support", "requests")) is not None
    with pytest.raises(PersistenceScopeError, match="Undeclared collection"):
        ctx.literal_collection("app_raw_records")


def test_platform_module_without_a_workspace_contract_is_still_bound_by_its_declarations():
    ctx = context("inbox", workspace=None)
    assert not refused(ctx, "inbox", "drafts")
    assert refused(ctx, "support", "requests")
    assert refused(ctx, "anything", "anything")


def test_workspace_can_declare_collections_for_a_platform_module():
    workspace = workspace_contract(shared_collections=[
        {"owner_module": "tasks", "name": "tasks", "read_by": [{"module": "support"}]},
        {"owner_module": "tasks", "mongo_collection": "legacy_task_archive", "read_by": [{"module": "support"}]},
    ])
    workspace["surfaces"].append({"surface_id": "support", "surface_kind": "module", "collections": [
        {"name": "escalations", "entity": "Escalation", "tenancy": "app_wide", "owner_field": None},
    ]})
    mounted().validate_workspace(workspace)
    support = context("support", workspace=workspace)
    assert not refused(support, "support", "escalations")
    assert not refused(support, "tasks", "tasks")
    assert support.literal_collection("legacy_task_archive") is not None
    inbox = context("inbox", workspace=workspace)
    assert refused(inbox, "tasks", "tasks")
    assert refused(inbox, "support", "escalations")


def test_an_overridden_platform_module_is_not_mounted_and_keeps_no_grants():
    # The workspace overrides `support`: it is an app module bound by the workspace contract.
    without_support = mounted("inbox", "tour")
    ctx = context("support", workspace=workspace_contract(), platform=without_support)
    assert refused(ctx, "inbox", "threads")
    assert refused(ctx, "support", "requests")  # undeclared by the workspace
    assert ("support", "requests") not in without_support.collections
    # The workspace overrides `inbox`: its data is the app's, so its share to `support` lapses.
    without_inbox = mounted("support", "tour")
    support = context("support", workspace=workspace_contract(), platform=without_inbox)
    assert refused(support, "inbox", "threads")
    assert not refused(support, "support", "requests")
    assert refused(context("inbox", workspace=workspace_contract(), platform=without_inbox), "inbox", "threads")


@pytest.mark.parametrize("redeclared", [
    {"surface_id": "support", "surface_kind": "module", "collections": [{"name": "requests"}]},
    {"surface_id": "Support", "surface_kind": "module", "collections": [{"name": "REQUESTS"}]},
])
def test_workspace_cannot_redeclare_a_mounted_platform_collection(redeclared):
    workspace = workspace_contract()
    workspace["surfaces"].append(redeclared)
    with pytest.raises(DataContractLoadError, match="redeclares platform module collection"):
        mounted().validate_workspace(workspace)


def test_platform_share_must_name_a_declared_collection():
    contract = platform_contract()
    contract["shared_collections"].append({"owner_module": "inbox", "name": "archive", "shared_with": [{"module": "support"}]})
    with pytest.raises(DataContractLoadError, match="which no mounted platform module declares"):
        PlatformModuleDeclarations(contract, ["support", "inbox"])


@pytest.mark.parametrize("principal", [None, PersistencePrincipal("")])
async def test_per_user_platform_collection_requires_the_authenticated_owner(principal):
    owned = context("tour", workspace=workspace_contract(), principal=PersistencePrincipal("user-a", "ws-a"))
    assert owned.collection("tour", "status")._owner_scope == {"user_id": "user-a"}
    anonymous = context("tour", workspace=workspace_contract(), principal=principal)
    with pytest.raises(PersistenceScopeError):
        await anonymous.collection("tour", "status").find_one({})


class _Recording:
    def __init__(self):
        self.calls = []

    async def update_one(self, query, update, **options):
        self.calls.append(query)

    async def find_one(self, query, projection=None, **options):
        self.calls.append(query)


async def test_owner_filter_that_repeats_the_principal_is_not_matched_twice():
    """Mongo rejects an upsert whose filter matches one path twice (error 54)."""
    raw = _Recording()
    owned = MongoPersistenceCollection(
        collection=raw, app_id="app-a", ownership=CollectionOwnership("per_user", "user_id"),
        principal=PersistencePrincipal("user-a"),
    )
    scope = {
        "app_id": "app-a", "user_id": "user-a",
        "$nor": [{"app_id": {"$type": "array"}}, {"user_id": {"$type": "array"}}],
    }
    await owned.update_one({"user_id": "user-a"}, {"$set": {"seen": True}}, upsert=True)
    await owned.update_one({"user_id": "user-a", "app_id": "app-a", "step": "one"}, {"$set": {"seen": True}})
    await owned.find_one({"user_id": "victim"})
    await owned.find_one({"user_id": {"$ne": "user-a"}})
    assert raw.calls == [
        scope,
        {"$and": [scope, {"step": "one"}]},
        {"$and": [scope, {"user_id": "victim"}]},
        {"$and": [scope, {"user_id": {"$ne": "user-a"}}]},
    ]


@pytest.mark.parametrize("module_id,workspace", [
    ("support", None),
    ("tasks", None),
    ("tasks", workspace_contract(tenancy="app_wide")),
    ("tasks", workspace_contract()),
])
@pytest.mark.parametrize("stage", [
    {"$lookup": {"from": "forbidden", "pipeline": [], "as": "rows"}},
    {"$unionWith": "forbidden"},
    {"$graphLookup": {"from": "forbidden", "startWith": "$id", "connectFromField": "id",
                      "connectToField": "id", "as": "rows"}},
    {"$out": "forbidden"},
    {"$merge": "forbidden"},
    {"$facet": {"rows": [{"$lookup": {"from": "forbidden", "pipeline": [], "as": "rows"}}]}},
])
async def test_platform_boundary_refuses_foreign_aggregation_even_without_owned_rows(module_id, workspace, stage):
    ctx = context(module_id, workspace=workspace, principal=PersistencePrincipal("user-a"))
    name = "requests" if module_id == "support" else "tasks"
    collection = ctx.collection(module_id, name)
    with pytest.raises(PersistenceScopeError):
        await collection.aggregate([stage])
    assert collection._collection.aggregate_pipelines == []


@pytest.mark.parametrize("module_id,workspace", [
    ("support", None),
    ("tasks", None),
    ("tasks", workspace_contract(tenancy="app_wide")),
])
async def test_platform_boundary_keeps_same_collection_aggregation_and_bounds_aliases(module_id, workspace):
    ctx = context(module_id, workspace=workspace)
    name = "requests" if module_id == "support" else "tasks"
    collection = ctx.collection(module_id, name)
    await collection.insert_one({"status": "open"})
    assert collection._collection.inserted == [{"status": "open", "app_id": "app-a"}]
    assert (await collection.find_one({"status": "open"}))["query"] == {"app_id": "app-a", "status": "open"}
    await collection.aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}])
    assert collection._collection.aggregate_pipelines[0][0] == {"$match": {"app_id": "app-a"}}
    alias = ctx.literal_collection(ctx.collection_name(module_id, name))
    assert isinstance(alias, GuardedAliasCollection)
    assert not hasattr(alias, "database")
    with pytest.raises(PersistenceScopeError):
        alias.aggregate([{"$unionWith": "forbidden"}])
    await alias.find_one({"app_id": "app-a"})
    await alias.aggregate([{"$match": {"app_id": "app-a"}}]).to_list(length=None)


@pytest.mark.parametrize("literal_target", [False, True])
def test_workspace_literal_share_cannot_grant_another_platform_modules_owned_collection(literal_target):
    contract = platform_contract()
    if literal_target:
        contract["surfaces"][2]["collections"][0]["mongo_collection"] = "private_tour_status"
    platform = PlatformModuleDeclarations(contract, ["support", "inbox", "tour"])
    target = "private_tour_status" if literal_target else context("tour").collection_name("tour", "status")
    workspace = workspace_contract(shared_collections=[{
        "owner_module": "tasks", "mongo_collection": target,
        "read_by": [{"module": "support"}],
    }])
    ctx = context("support", workspace=workspace, platform=platform)
    with pytest.raises(PersistenceScopeError):
        ctx.literal_collection(target)


# --------------------------------------------------------------------------- the Studio declarations


def test_factory_declares_each_studio_collection_with_complete_ownership():
    contract = load_data_contract(FACTORY)
    assert contract is not None
    validate_complete_data_contract_ownership({**contract, "shared_collections": []})
    declarations = PlatformModuleDeclarations(contract, STUDIO_MODULES)
    assert declarations.collections == STUDIO_COLLECTIONS
    tenancy = {
        (surface["surface_id"], row["name"]): (row["scope"], row["tenancy"], row["owner_field"])
        for surface in contract["surfaces"] for row in surface["collections"]
    }
    assert tenancy == {
        ("messages", "messages"): ("platform", "app_wide", None),
        ("messages", "thread_reads"): ("platform", "app_wide", None),
        ("messages", "threads"): ("platform", "app_wide", None),
        ("security_readiness", "findings"): ("platform", "per_user", "owner_user_id"),
        ("user_onboarding", "status"): ("platform", "per_user", "user_id"),
        ("workspace_integrations", "integration_notes"): ("platform", "app_wide", None),
        ("workspace_support", "feedback"): ("platform", "app_wide", None),
        ("workspace_support", "requests"): ("platform", "app_wide", None),
    }
    # Indexes stay with the factory migrations; mounting a module creates none in a workspace.
    assert all(row["indexes"] == [] for surface in contract["surfaces"] for row in surface["collections"])


def app_root(parent: Path, name: str, contract: dict | None) -> Path:
    root = parent / name / "app"
    root.mkdir(parents=True)
    (root / "app.json").write_text(json.dumps({"appName": name, "appId": name}), encoding="utf-8")
    if contract is not None:
        (root / "data").mkdir()
        (root / "data/contract.json").write_text(json.dumps(contract), encoding="utf-8")
    return root


def module(root: Path, name: str, reads: dict[str, tuple[str, str]]) -> None:
    target = root / "modules" / name
    (target / "backend").mkdir(parents=True)
    actions = "".join(
        f"  - id: {action}\n    description: Read {owner}.{collection}.\n    handler_method: {action}\n"
        for action, (owner, collection) in reads.items()
    )
    (target / "module.yaml").write_text(
        f"schema_version: mozaiks.module.v1\nmodule:\n  id: {name}\n  version: 1.0.0\n"
        f"  handler: backend.handler:Handler\nactions:\n{actions}",
        encoding="utf-8",
    )
    methods = "".join(
        f"    async def {action}(self, ctx):\n"
        f"        await ctx.persistence.collection({owner!r}, {collection!r}).find_one({{}})\n"
        f"        return {{'read': {owner + '.' + collection!r}}}\n"
        for action, (owner, collection) in reads.items()
    )
    (target / "backend/handler.py").write_text(f"class Handler:\n{methods}", encoding="utf-8")


async def test_studio_mounts_the_declarations_of_the_factory_modules_it_composes(tmp_path):
    active = app_root(tmp_path, "my-app", SCAFFOLD_CONTRACT)
    loaded = await AppLoader.load(str(active), module_defaults_path=str(FACTORY))
    assert not loaded.failed_module_names
    assert loaded.data_contract == SCAFFOLD_CONTRACT
    assert loaded.platform_modules is not None
    assert loaded.platform_modules.module_ids == STUDIO_MODULES
    assert loaded.platform_modules.collections == STUDIO_COLLECTIONS


async def test_overriding_a_factory_module_unmounts_its_declarations(tmp_path):
    active = app_root(tmp_path, "my-app", SCAFFOLD_CONTRACT)
    module(active, "workspace_support", {"read": ("workspace_support", "requests")})
    loaded = await AppLoader.load(str(active), module_defaults_path=str(FACTORY))
    assert "workspace_support" not in loaded.platform_modules.module_ids
    assert loaded.platform_modules.collections == STUDIO_COLLECTIONS - {
        ("workspace_support", "feedback"), ("workspace_support", "requests"),
    }


async def test_the_platform_host_and_the_factory_as_active_root_mount_nothing(tmp_path):
    active = app_root(tmp_path, "my-app", SCAFFOLD_CONTRACT)
    assert (await AppLoader.load(str(active))).platform_modules is None
    factory = await AppLoader.load(str(FACTORY), module_defaults_path=str(FACTORY))
    # Source-checkout Studio: the factory bundle is the workspace, and its contract the allow-list.
    assert factory.platform_modules is None
    assert factory.data_contract == load_data_contract(FACTORY)


async def test_a_workspace_redeclaring_a_studio_collection_fails_to_load(tmp_path):
    active = app_root(tmp_path, "my-app", {"version": "1", "surfaces": [
        {"surface_id": "workspace_support", "surface_kind": "module", "collections": [{"name": "requests"}]},
    ]})
    with pytest.raises(AppLoadError, match="redeclares platform module collection workspace_support.requests"):
        await AppLoader.load(str(active), module_defaults_path=str(FACTORY))


def authority(actor: str = "user-a", *permissions: str) -> ModuleDispatchAuthority:
    return ModuleDispatchAuthority(
        kind="authenticated_user", permission_mode="enforce", reason="test user",
        actor_id=actor, permissions=permissions,
    )


def executor_for(loaded, monkeypatch, *, client, database: str = "platform_module_test") -> ModuleExecutor:
    executor = ModuleExecutor(
        data_contract=loaded.data_contract, platform_modules=loaded.platform_modules,
        persistence_client=client, persistence_database=database,
    )
    monkeypatch.setattr(executor, "_emit_dispatch_audit", AsyncMock())
    for item in loaded.modules:
        executor.register_loaded_module(item)
    return executor


async def test_dispatch_binds_each_module_to_its_own_allow_list(tmp_path, monkeypatch):
    active = app_root(tmp_path, "my-app", workspace_contract(tenancy="app_wide"))
    module(active, "tasks", {
        "own": ("tasks", "tasks"), "platform": ("probe", "items"), "undeclared": ("tasks", "secrets"),
    })
    defaults = app_root(tmp_path, "defaults", {"version": "1", "surfaces": [
        {"surface_id": "probe", "surface_kind": "module", "collections": [
            {"name": "items", "entity": "Item", "tenancy": "app_wide", "owner_field": None},
        ]},
        {"surface_id": "neighbour", "surface_kind": "module", "collections": [
            {"name": "items", "entity": "Item", "tenancy": "app_wide", "owner_field": None},
        ]},
    ]})
    module(defaults, "probe", {
        "own": ("probe", "items"), "app": ("tasks", "tasks"), "neighbour": ("neighbour", "items"),
    })
    module(defaults, "neighbour", {"own": ("neighbour", "items")})
    loaded = await AppLoader.load(str(active), module_defaults_path=str(defaults))
    assert loaded.platform_modules.module_ids == {"probe", "neighbour"}
    executor = executor_for(loaded, monkeypatch, client=FakeMongoClient())

    async def run(module_id: str, action: str):
        return await executor.execute(ModuleRequest(
            module=module_id, action=action, app_id="my-app", user_id="user-a",
            authority=authority(), persistence_principal=PersistencePrincipal("user-a", "ws-a"),
        ))

    outcomes = {
        (module_id, action): (await run(module_id, action)).error_code or "OK"
        for module_id, action in (
            ("tasks", "own"), ("tasks", "platform"), ("tasks", "undeclared"),
            ("probe", "own"), ("probe", "app"), ("probe", "neighbour"), ("neighbour", "own"),
        )
    }
    assert outcomes == {
        ("tasks", "own"): "OK",
        ("tasks", "platform"): "PERMISSION_DENIED",
        ("tasks", "undeclared"): "PERMISSION_DENIED",
        ("probe", "own"): "OK",
        ("probe", "app"): "PERMISSION_DENIED",
        ("probe", "neighbour"): "PERMISSION_DENIED",
        ("neighbour", "own"): "OK",
    }


# --------------------------------------------------------------------------- real Mongo


@pytest.fixture
async def mongo():
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("MONGO_URI")
    if not uri:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MONGO_URI is not set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MongoDB is unavailable")
    database = f"platform_module_test_{uuid.uuid4().hex[:12]}"
    yield SimpleNamespace(client=client, database=database)
    await client.drop_database(database)
    client.close()


STUDIO_PERMISSIONS = (
    "workspace_integrations.read", "workspace_integrations.manage", "workspace_support.read",
    "workspace_support.manage", "security_readiness.read", "security_readiness.manage",
    "onboarding.read", "onboarding.manage", "messages.read", "messages.write",
)


async def test_app_wide_platform_boundary_on_mongo_preserves_crud_and_refuses_foreign_stages(mongo):
    ctx = MongoPersistenceContext(
        app_id="app-a", client=mongo.client, database_name=mongo.database,
        platform_modules=mounted(), module_id="support",
    )
    own = ctx.collection("support", "requests")
    foreign = MongoPersistenceContext(
        app_id="app-a", client=mongo.client, database_name=mongo.database,
        platform_modules=mounted(), module_id="tour", principal=PersistencePrincipal("user-a"),
    )
    protected = foreign.collection("tour", "status")
    await protected.insert_one({"secret": "owner-only"})
    await own.insert_one({"request_id": "r1", "status": "open"})
    await own.update_one({"request_id": "r1"}, {"$set": {"status": "resolved"}})
    assert (await own.find_one({"request_id": "r1"}))["status"] == "resolved"
    assert await own.aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}]) == [
        {"_id": "resolved", "count": 1},
    ]
    target = foreign.collection_name("tour", "status")
    for stage in (
        {"$lookup": {"from": target, "pipeline": [], "as": "rows"}},
        {"$unionWith": target}, {"$out": target}, {"$merge": target},
        {"$facet": {"rows": [{"$unionWith": target}]}},
    ):
        with pytest.raises(PersistenceScopeError):
            await own.aggregate([stage])
    alias = ctx.literal_collection(ctx.collection_name("support", "requests"))
    assert not hasattr(alias, "database")
    with pytest.raises(PersistenceScopeError):
        alias.aggregate([{"$unionWith": target}])
    rows = await alias.aggregate([{"$match": {"app_id": "app-a"}}]).to_list(length=None)
    assert [row["request_id"] for row in rows] == ["r1"]
    assert (await protected.find_one({}))["secret"] == "owner-only"
    await own.delete_one({"request_id": "r1"})
    assert await own.count({}) == 0


@pytest.mark.parametrize("module_id,workspace", [
    ("support", None),  # a mounted built-in module's own alias handle
    ("tasks", workspace_contract(tenancy="app_wide")),  # an app module's own alias handle
])
async def test_module_alias_handles_refuse_driver_option_routes_on_mongo(module_id, workspace, mongo):
    """Each module's bounded alias handle forwards only driver-defined options (follow-up to #798).

    The handle wraps a real driver collection. It refuses an unexpected option,
    a surplus positional argument, or a raw query command envelope in a read
    filter before the driver builds a command, so the operation cannot reach a
    collection or database the module must not touch, while a driver-defined
    option still works.
    """
    other_db = f"{mongo.database}_handle_other"
    ctx = MongoPersistenceContext(
        app_id="app-a", client=mongo.client, database_name=mongo.database,
        data_contract=workspace, platform_modules=mounted(), module_id=module_id,
        principal=PersistencePrincipal("user-a", "ws-a"),
    )
    name = "requests" if module_id == "support" else "tasks"
    handle = ctx.literal_collection(ctx.collection_name(module_id, name))
    assert isinstance(handle, GuardedAliasCollection)
    try:
        foreign = mongo.client[mongo.database]["foreign_rows"]
        await foreign.insert_many([{"k": 1, "marker": "foreign"}, {"k": 2, "marker": "foreign"}])
        await mongo.client[other_db]["foreign_rows"].insert_one({"k": 1, "marker": "other-db"})

        async def refused(coro):
            with pytest.raises(PersistenceScopeError):
                await coro

        await refused(handle.count_documents({}, pipeline=[{"$collStats": {}}]))
        await refused(handle.distinct("marker", distinct="foreign_rows"))
        await refused(handle.find_one_and_update({}, {"$set": {"marker": "x"}}, findAndModify="foreign_rows"))
        await refused(handle.find_one_and_delete({}, findAndModify="foreign_rows"))
        await refused(handle.find_one({"$query": {}, "find": "foreign_rows"}))
        with pytest.raises(PersistenceScopeError):
            handle.aggregate([{"$match": {}}], aggregate="foreign_rows")
        with pytest.raises(PersistenceScopeError):
            handle.find({"$query": {}, "$db": other_db, "find": "foreign_rows"})

        # The foreign collection and the second database are untouched.
        assert await foreign.count_documents({}) == 2
        assert await foreign.count_documents({"marker": {"$ne": "foreign"}}) == 0
        assert await mongo.client[other_db]["foreign_rows"].count_documents({}) == 1

        # A legitimate option on the module's own alias handle still works.
        await handle.insert_one({"state": "open"})
        assert await handle.count_documents({"state": "open"}, limit=5) == 1
    finally:
        await mongo.client.drop_database(other_db)


async def test_studio_pages_load_in_a_fresh_scaffold_and_keep_users_apart(tmp_path, monkeypatch, mongo):
    active = app_root(tmp_path, "my-app", workspace_contract())
    module(active, "tasks", {"own": ("tasks", "tasks"), "support": ("workspace_support", "requests")})
    loaded = await AppLoader.load(str(active), module_defaults_path=str(FACTORY))
    integrations = next(item for item in loaded.modules if item.name == "workspace_integrations")
    # Usage counts come from the system database, outside any app data contract.
    integrations.handler.service.declarations_repo = SimpleNamespace(get_catalog_usage_counts=AsyncMock(return_value={}))
    executor = executor_for(loaded, monkeypatch, client=mongo.client, database=mongo.database)

    async def run(module_id, action, params=None, *, user="user-a", app_id="my-app", signed_in=True):
        return await executor.execute(ModuleRequest(
            module=module_id, action=action, params=params or {}, app_id=app_id, user_id=user,
            authority=authority(user, *STUDIO_PERMISSIONS),
            persistence_principal=PersistencePrincipal(user, "ws-a") if signed_in else None,
        ))

    def ok(result):
        assert result.success, (result.error_code, result.error)
        return result.data

    # The requests the Integrations and Support pages make (issue #790).
    assert ok(await run("workspace_integrations", "set_integration_note", {"integration_id": "openai", "note": "rotated"}))
    listed = ok(await run("workspace_integrations", "list_integrations"))
    assert next(item["note"] for item in listed["integrations"] if item["id"] == "openai") == "rotated"
    created = ok(await run("workspace_support", "create_support_request", {"message": "help", "subject_app_id": "my-app"}))
    for params in ({"status": "all", "limit": 200, "scope": "workspace"},
                   {"status": "all", "limit": 50, "scope": "app", "subject_app_id": "my-app"}):
        requests = ok(await run("workspace_support", "list_support_requests", params))["requests"]
        assert [row["request_id"] for row in requests] == [created["request_id"]]
    assert ok(await run("workspace_support", "submit_session_feedback", {"session_id": "s1"}))
    assert ok(await run("messages", "list_threads")) is not None
    database = mongo.client[mongo.database]
    names = await database.list_collection_names()
    support = executor._build_persistence_context(ModuleRequest(
        module="workspace_support", action="list_support_requests", app_id="my-app", authority=authority(),
    ))
    for module_id, name in (("workspace_support", "feedback"), ("messages", "threads")):
        assert await database[support.collection_name(module_id, name)].count_documents({}) == 1, names

    # Per-user platform records stay with their owner.
    for user in ("user-a", "user-b"):
        ok(await run("user_onboarding", "get_onboarding_status", user=user))
    ok(await run("user_onboarding", "complete_step", {"step_id": "create_app"}, user="user-b"))
    progress = {user: ok(await run("user_onboarding", "get_onboarding_status", user=user))["progress"]
                for user in ("user-a", "user-b")}
    assert progress["user-a"] == 0 and progress["user-b"] > 0
    ok(await run("security_readiness", "record_assessment", {"app_id": "my-app", "findings": [
        {"finding_id": "f1", "title": "Review auth", "severity": "high", "control_area": "auth"},
    ]}))
    assert ok(await run("security_readiness", "list_findings", {"app_id": "my-app"}))["count"] == 1
    assert ok(await run("security_readiness", "list_findings", {"app_id": "my-app"}, user="user-b"))["count"] == 0
    unsigned = await run("user_onboarding", "get_onboarding_status", signed_in=False)
    assert unsigned.error_code == "PERMISSION_DENIED"

    # App partition and the app's own isolation are unchanged.
    other_app = ok(await run("workspace_support", "list_support_requests",
                             {"status": "all", "scope": "workspace"}, app_id="other-app"))
    assert other_app["requests"] == []
    assert ok(await run("tasks", "own"))
    assert (await run("tasks", "support")).error_code == "PERMISSION_DENIED"


async def test_owned_upsert_with_the_owner_in_its_filter_succeeds_on_mongo(mongo):
    ctx = MongoPersistenceContext(
        app_id="app-a", client=mongo.client, database_name=mongo.database,
        data_contract=workspace_contract(), principal=PersistencePrincipal("user-a"),
    )
    tasks = ctx.collection("tasks", "tasks")
    await tasks.update_one({"user_id": "user-a"}, {"$set": {"user_id": "user-a", "title": "one"}}, upsert=True)
    await tasks.update_one({"user_id": "user-a", "task_id": "t2"}, {"$set": {"title": "two"}}, upsert=True)
    rows = await tasks.find_many({}, sort=[("title", 1)])
    assert [(row["title"], row["user_id"], row["app_id"]) for row in rows] == [
        ("one", "user-a", "app-a"), ("two", "user-a", "app-a"),
    ]
    with pytest.raises(PersistenceScopeError):
        await tasks.update_one({"user_id": "user-a"}, {"$set": {"user_id": "victim"}}, upsert=True)
