"""Handler reactions use declared storage without treating event identity as authority."""

from __future__ import annotations

import asyncio
import os
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest

from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.persistence import (
    MongoPersistenceContext,
    PersistencePrincipal,
    PersistenceScopeError,
    PlatformModuleDeclarations,
)
from mozaiksai.core.runtime.persistence.request_scope import bind_persistence_principal
from tests.module_authority_test_helpers import trusted_framework_authority
from tests.test_module_event_router import (
    _handler_target_reaction,
    _loaded_module,
    _with_definition,
    _with_event_schema,
)

EVENT = "domain.preferences.requested"
MODULE = "preferences"


def contract(tenancy="app_wide"):
    owner_field = {"per_user": "user_id", "per_workspace": "workspace_id"}.get(tenancy)
    return {"version": "1", "surfaces": [{
        "surface_id": MODULE, "surface_kind": "module", "collections": [{
            "name": "preferences", "entity": "Preference", "tenancy": tenancy,
            "owner_field": owner_field,
        }],
    }], "shared_collections": []}


def event(*, app_id="app-a", user_id="alice", workspace_id="workspace-a", **payload):
    return {
        "id": str(uuid4()), "type": EVENT, "version": 1,
        "tenant": {"app_id": app_id, "workspace_id": workspace_id},
        "actor": {"type": "user", "id": user_id},
        "payload": payload,
    }


class PreferencesHandler:
    def __init__(self):
        self.contexts = []
        self.rows = []

    async def on_event(self, ctx, *, recipient="alice", target=MODULE, collection="preferences"):
        self.contexts.append(ctx)
        self.rows.append(await ctx.persistence.collection(target, collection).find_one({"user_id": recipient}))


def router(handler, **kwargs):
    module = _with_definition(_with_event_schema(_loaded_module(
        MODULE, handler=handler, reactions=[_handler_target_reaction(EVENT)],
    ), event_type=EVENT, payload_schema={"type": "object"}))
    return ModuleEventRouter([module], **kwargs)


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
    database = f"reaction_persistence_{uuid4().hex}"
    try:
        yield SimpleNamespace(client=client, database=database)
    finally:
        await client.drop_database(database)
        client.close()


def configured_router(handler, mongo, *, tenancy="app_wide", platform_modules=None):
    return router(
        handler, data_contract=contract(tenancy) if platform_modules is None else None,
        platform_modules=platform_modules,
        persistence_client=mongo.client, persistence_database=mongo.database,
    )


async def seed(mongo, tenancy="app_wide"):
    for app_id in ("app-a", "app-b"):
        for user, workspace in (("alice", "workspace-a"), ("bob", "workspace-b")):
            ctx = MongoPersistenceContext(
                app_id=app_id, client=mongo.client, database_name=mongo.database,
                data_contract=contract(tenancy), principal=PersistencePrincipal(user, workspace),
            )
            await ctx.collection(MODULE, "preferences").insert_one({
                "user_id": user, "workspace_id": workspace, "email_enabled": False,
            })


async def test_unconfigured_router_has_explicit_absent_persistence():
    seen = []

    async def inspect_context(ctx, **payload):
        seen.append((ctx.persistence, ctx.module_id, ctx.workspace_id))

    result = await router(SimpleNamespace(on_event=inspect_context)).handle_event(EVENT, event())
    assert result.success
    assert seen == [(None, MODULE, "workspace-a")]


@pytest.mark.parametrize("active_principal", [False, True])
async def test_app_wide_preferences_keep_recipient_and_app_scope(mongo, active_principal):
    await seed(mongo)
    handler = PreferencesHandler()
    runtime = configured_router(handler, mongo)
    scope = bind_persistence_principal("app-a", PersistencePrincipal("alice", "workspace-a"))
    with scope if active_principal else nullcontext():
        result = await runtime.handle_event(EVENT, event(recipient="bob"))
    assert result.success
    assert handler.rows[0]["app_id"] == "app-a"
    assert handler.rows[0]["user_id"] == "bob"
    assert handler.rows[0]["email_enabled"] is False
    ctx = handler.contexts[0]
    assert ctx.user_id == "alice" and ctx.module_id == MODULE
    assert ctx.permissions == []
    assert not hasattr(ctx, "db")


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
@pytest.mark.parametrize("principal_app", [None, "app-b"])
async def test_event_actor_and_permissions_cannot_supply_missing_principal(mongo, tenancy, principal_app):
    await seed(mongo, tenancy)
    handler = PreferencesHandler()
    runtime = configured_router(handler, mongo, tenancy=tenancy)
    envelope = event()
    envelope["authority"] = {"permissions": ["preferences.manage"]}
    scope = bind_persistence_principal(principal_app, PersistencePrincipal("alice", "workspace-a"))
    with scope if principal_app else nullcontext():
        result = await runtime.handle_event(EVENT, envelope)
    assert result.reactions[0].status == "failed"
    assert result.reactions[0].reason == "PersistenceScopeError"
    assert handler.rows == []


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
async def test_active_principal_scopes_owned_reads_and_rejects_other_owner_writes(mongo, tenancy):
    await seed(mongo, tenancy)
    handler = PreferencesHandler()
    runtime = configured_router(handler, mongo, tenancy=tenancy)
    with bind_persistence_principal("app-a", PersistencePrincipal("alice", "workspace-a")):
        assert (await runtime.handle_event(EVENT, event())).success
        assert handler.rows[-1]["user_id"] == "alice"
        assert (await runtime.handle_event(EVENT, event(recipient="bob"))).success
        assert handler.rows[-1] is None
        collection = handler.contexts[-1].persistence.collection(MODULE, "preferences")
        owner_field, other = ("user_id", "bob") if tenancy == "per_user" else ("workspace_id", "workspace-b")
        with pytest.raises(PersistenceScopeError):
            await collection.insert_one({owner_field: other})
    assert await mongo.client[mongo.database][collection._collection.name].count_documents({}) == 2


@pytest.mark.parametrize("mismatch", ["actor", "workspace"])
async def test_event_identity_mismatch_does_not_silently_use_current_principal(mongo, mismatch):
    await seed(mongo, "per_user")
    handler = PreferencesHandler()
    runtime = configured_router(handler, mongo, tenancy="per_user")
    envelope = event(user_id="bob") if mismatch == "actor" else event(workspace_id="workspace-b")
    with bind_persistence_principal("app-a", PersistencePrincipal("alice", "workspace-a")):
        result = await runtime.handle_event(EVENT, envelope)
    assert result.reactions[0].status == "failed"
    assert result.reactions[0].reason == "PersistenceScopeError"
    assert handler.rows == []


async def test_inherited_background_task_cannot_keep_request_principal(mongo):
    await seed(mongo, "per_user")
    ready = asyncio.Event()
    handler = PreferencesHandler()
    runtime = configured_router(handler, mongo, tenancy="per_user")

    async def delayed():
        await ready.wait()
        return await runtime.handle_event(EVENT, event())

    with bind_persistence_principal("app-a", PersistencePrincipal("alice", "workspace-a")):
        task = asyncio.create_task(delayed())
    ready.set()
    result = await task
    assert result.reactions[0].reason == "PersistenceScopeError"
    assert handler.rows == []


async def test_action_emit_keeps_active_principal_only_until_dispatch_returns(mongo):
    await seed(mongo, "per_user")
    handler = PreferencesHandler()
    runtime = configured_router(handler, mongo, tenancy="per_user")

    async def publish(ctx):
        await ctx.emit(EVENT, {"recipient": "alice"})
        return {"success": True}

    executor = ModuleExecutor(event_emitter=runtime.handle_event)
    executor.register(
        "producer", SimpleNamespace(publish=publish), action_method_map={"publish": "publish"},
        action_emits={"publish": [EVENT]}, event_payload_schemas={EVENT: {"type": "object"}},
    )
    result = await executor.execute(ModuleRequest(
        module="producer", action="publish", app_id="app-a", user_id="alice", workspace_id="workspace-a",
        persistence_principal=PersistencePrincipal("alice", "workspace-a"), authority=trusted_framework_authority(),
    ))
    assert result.success
    assert handler.rows[0]["user_id"] == "alice"
    saved = handler.contexts[0].persistence.collection(MODULE, "preferences")
    with pytest.raises(PersistenceScopeError):
        await saved.find_one({})


@pytest.mark.parametrize("target,collection", [(MODULE, "undeclared"), ("private_host", "secrets")])
async def test_module_allow_list_rejects_undeclared_and_other_platform_storage(mongo, target, collection):
    platform_contract = contract()
    platform_contract["surfaces"].append({
        "surface_id": "private_host", "surface_kind": "module", "collections": [{
            "name": "secrets", "entity": "Secret", "tenancy": "app_wide", "owner_field": None,
        }],
    })
    declarations = PlatformModuleDeclarations(platform_contract, (MODULE, "private_host"))
    handler = PreferencesHandler()
    runtime = configured_router(handler, mongo, platform_modules=declarations)
    result = await runtime.handle_event(EVENT, event(target=target, collection=collection))
    assert result.reactions[0].reason == "PersistenceScopeError"
    assert handler.rows == []
    assert await mongo.client[mongo.database].list_collection_names() == []


async def test_platform_reaction_can_use_only_its_loaded_collection_declaration(mongo):
    await seed(mongo)
    handler = PreferencesHandler()
    runtime = configured_router(handler, mongo, platform_modules=PlatformModuleDeclarations(contract(), (MODULE,)))
    result = await runtime.handle_event(EVENT, event())
    assert result.success
    assert handler.rows[0]["app_id"] == "app-a"
    assert handler.rows[0]["email_enabled"] is False


async def test_empty_loaded_contract_is_bounded_not_an_unconfigured_router(mongo):
    handler = PreferencesHandler()
    runtime = router(
        handler, data_contract={"surfaces": [], "shared_collections": []},
        persistence_client=mongo.client, persistence_database=mongo.database,
    )
    result = await runtime.handle_event(EVENT, event())
    assert result.reactions[0].reason == "PersistenceScopeError"
    assert handler.rows == []
    assert await mongo.client[mongo.database].list_collection_names() == []
