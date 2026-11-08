"""Reaction emissions retain their declared producer and explicit refusal outcome."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from mozaiksai.core.events.unified_event_dispatcher import UnifiedEventDispatcher
from mozaiksai.core.runtime.app.module_loader import (
    LoadedModule,
    ModuleCompanionManifests,
    ModuleDefinition,
)
from mozaiksai.core.runtime.composition.module_event_provenance import ModuleEventRejection
from mozaiksai.core.runtime.composition.module_event_router import (
    ModuleEventRouter,
    ModuleReactionEffectAck,
)
from mozaiksai.core.runtime.composition.workflow_trigger_guard import (
    WORKFLOW_TRIGGER_TRACE_KEY,
    WorkflowTriggerGuard,
)

SOURCE = "domain.orders.changed"
OUTPUT = "domain.feed.recorded"
SCHEMA = {
    "type": "object",
    "required": ["record_id"],
    "properties": {"record_id": {"type": "string"}},
}


def module(handler, *, actions=None, producer="feed", schema=None):
    definition = ModuleDefinition.model_validate(
        {
            "schema_version": "mozaiks.module.v1",
            "module": {
                "id": "feed",
                "display_name": "Feed",
                "version": "1",
                "description": "Feed",
                "handler": "backend.handler:Handler",
            },
            "actions": actions
            if actions is not None
            else [
                {
                    "id": "process_order",
                    "description": "Record order activity",
                    "handler_method": "on_order",
                    "api_surface": "internal",
                    "permissions": [],
                    "emits": [OUTPUT],
                }
            ],
        }
    )
    manifests = ModuleCompanionManifests.model_validate(
        {
            "events": {
                "events": [
                    {
                        "type": OUTPUT,
                        "version": 1,
                        "producer": producer,
                        "payload_schema": SCHEMA if schema is None else schema,
                    }
                ]
            },
            "reactions": {
                "reactions": [
                    {
                        "id": "order_activity",
                        "event_type": SOURCE,
                        "target": {"kind": "handler", "handler_method": "on_order"},
                        "permissions": ["orders.read"],
                    }
                ]
            },
        }
    )
    return LoadedModule(definition, handler, Path("unused"), manifests)


def source():
    return {
        "id": "source-event",
        "type": SOURCE,
        "version": 1,
        "source": {
            "layer": "module",
            "module_id": "orders",
            "action_id": "update",
            "app_id": "app-a",
        },
        "tenant": {"app_id": "app-a", "tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        "actor": {"type": "user", "id": "alice"},
        "correlation": {"correlation_id": "journey", "causation_id": "earlier-event"},
        "authority": {"kind": "user", "permission_mode": "enforce", "permissions": ["orders.read"]},
        "payload": {},
    }


async def dispatch(loaded, envelope=None):
    dispatcher = UnifiedEventDispatcher()
    observed = []

    async def observe(value):
        observed.append(value)

    dispatcher.register_handler(OUTPUT, observe)
    runtime = ModuleEventRouter(
        loaded if isinstance(loaded, list) else [loaded], event_emitter=dispatcher.emit
    )
    runtime.register(dispatcher)
    receipt = await dispatcher.emit(SOURCE, source() if envelope is None else envelope)
    result = next(
        listener.result for listener in receipt.listeners if hasattr(listener.result, "reactions")
    )
    return result, observed


async def test_allowed_emit_binds_producer_identity_and_immediate_causation():
    replies = []

    async def on_order(ctx):
        # Custom code may modify its public context; the emission binding is captured.
        ctx.app_id = "forged-app"
        ctx.user_id = "mallory"
        ctx.permissions.append("root")
        replies.append(
            await ctx.emit(
                OUTPUT,
                {
                    "record_id": "r-1",
                    "source": {"module_id": "forged"},
                    "authority": {"permissions": ["root"]},
                },
            )
        )

    result, observed = await dispatch(module(SimpleNamespace(on_order=on_order)))
    assert result.success and replies[0].success
    [event] = observed
    assert event["source"] == {
        "layer": "module",
        "app_id": "app-a",
        "module_id": "feed",
        "action_id": "process_order",
        "capability_id": "feed.process_order",
    }
    assert event["tenant"] == {
        "app_id": "app-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
    }
    assert event["actor"] == {"type": "user", "id": "alice"}
    assert event["correlation"] == {"correlation_id": "journey", "causation_id": "source-event"}
    assert event["authority"]["permissions"] == ["orders.read"]
    assert event["payload"]["authority"]["permissions"] == ["root"]
    assert event["id"] != "source-event" and event["version"] == 1


@pytest.mark.parametrize(
    "defect,category",
    [
        ("missing_action", "undeclared"),
        ("public_action", "undeclared"),
        ("permissioned_action", "undeclared"),
        ("ambiguous_action", "undeclared"),
        ("undeclared_event", "undeclared"),
        ("foreign_producer", "undeclared"),
        ("invalid_payload", "value_invalid"),
        ("invalid_schema", "schema_invalid"),
    ],
)
async def test_rejected_emission_is_returned_and_visible_without_rolling_back_handler(
    defect, category
):
    writes, replies = [], []

    async def on_order(ctx):
        writes.append("committed-effect")
        payload = (
            {"record_id": 123, "private": "DO-NOT-LOG"}
            if defect == "invalid_payload"
            else {"record_id": "r-1"}
        )
        replies.append(await ctx.emit(OUTPUT, payload))
        return ModuleReactionEffectAck("committed-effect")

    loaded = module(
        SimpleNamespace(on_order=on_order),
        schema={"type": "invalid"} if defect == "invalid_schema" else None,
    )
    action = loaded.definition.actions[0]
    if defect == "missing_action":
        loaded.definition.actions.clear()
    elif defect == "public_action":
        action.api_surface = "public"
    elif defect == "permissioned_action":
        action.permissions = ["feed.admin"]
    elif defect == "ambiguous_action":
        loaded.definition.actions.append(action.model_copy(update={"id": "second"}))
    elif defect == "undeclared_event":
        action.emits.clear()
    elif defect == "foreign_producer":
        loaded.manifests.events.events[0].producer = "orders"
    result, observed = await dispatch(loaded)
    [rejection] = replies
    assert isinstance(rejection, ModuleEventRejection)
    assert rejection.category == category
    assert "DO-NOT-LOG" not in json.dumps(rejection.to_dict())
    assert observed == [] and writes == ["committed-effect"]
    [delivery] = result.reactions
    assert delivery.status == "failed" and not delivery.success and delivery.effect_id is None
    assert category in delivery.reason


async def test_non_emitting_callback_does_not_require_action_declaration():
    called = []

    async def on_order(ctx):
        called.append(ctx.user_id)

    result, observed = await dispatch(module(SimpleNamespace(on_order=on_order), actions=[]))
    assert result.success and called == ["alice"] and observed == []


async def test_emitted_event_does_not_borrow_another_modules_schema():
    replies = []

    async def on_order(ctx):
        replies.append(await ctx.emit(OUTPUT, {"record_id": "r-1"}))

    loaded = module(SimpleNamespace(on_order=on_order))
    loaded.manifests.events.events.clear()
    other = module(SimpleNamespace())
    other.definition.module.id = "other"
    other.manifests.events.events[0].producer = "other"
    other.manifests.reactions.reactions.clear()
    result, observed = await dispatch([loaded, other])
    assert isinstance(replies[0], ModuleEventRejection)
    assert replies[0].category == "undeclared" and observed == [] and not result.success


async def test_non_user_source_actor_type_is_preserved():
    async def on_order(ctx):
        await ctx.emit(OUTPUT, {"record_id": "r-1"})

    envelope = source()
    envelope["actor"] = {"type": "service", "id": "service-a"}
    result, observed = await dispatch(module(SimpleNamespace(on_order=on_order)), envelope)
    assert result.success and observed[0]["actor"] == envelope["actor"]


@pytest.mark.parametrize(
    "next_capability,trace,reason",
    [
        (
            "orders.review",
            {
                "root_event_id": "root",
                "depth": 1,
                "capability_ids": ["orders.review"],
                "invocation_ids": ["invocation"],
            },
            "cycle",
        ),
        (
            "orders.next",
            {
                "root_event_id": "root",
                "depth": 1,
                "capability_ids": ["orders.review"],
                "invocation_ids": ["invocation"],
            },
            "depth",
        ),
        ("orders.next", "malformed-lineage", "depth"),
    ],
)
async def test_reaction_emission_preserves_workflow_trigger_guard(next_capability, trace, reason):
    envelope = source()
    envelope[WORKFLOW_TRIGGER_TRACE_KEY] = deepcopy(trace)

    async def on_order(ctx):
        # Neither mutable source data nor an outgoing payload can reset ancestry.
        if isinstance(envelope[WORKFLOW_TRIGGER_TRACE_KEY], dict):
            envelope[WORKFLOW_TRIGGER_TRACE_KEY]["capability_ids"].clear()
        await ctx.emit(OUTPUT, {"record_id": "r-1", WORKFLOW_TRIGGER_TRACE_KEY: {"depth": 0}})

    result, [emitted] = await dispatch(module(SimpleNamespace(on_order=on_order)), envelope)
    assert result.success and emitted[WORKFLOW_TRIGGER_TRACE_KEY] == trace
    decision = await WorkflowTriggerGuard(
        claim_store=None, rate_limiter=None, max_depth=1
    ).authorize(
        capability_id=next_capability,
        source_event=emitted,
        app_id="app-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    assert not decision.allowed and decision.reason == reason


@pytest.mark.parametrize(
    "event_type,action_id,payload",
    [
        (
            "domain.social.friendship.created",
            "on_friendship_created",
            {
                "friendship_id": "f1",
                "user_a_id": "alice",
                "user_b_id": "bob",
                "requester_id": "alice",
                "request_id": "req1",
            },
        ),
        (
            "domain.social.post.published",
            "on_post_published",
            {"post_id": "p1", "author_id": "alice"},
        ),
        (
            "domain.social.post.commented",
            "on_post_commented",
            {"post_id": "p1", "post_author_id": "bob", "comment_id": "c1", "author_id": "alice"},
        ),
    ],
)
async def test_social_reaction_service_emits_own_contract_and_stays_http_internal(
    event_type, action_id, payload
):
    from factory_app.build_context.social.templates.modules.activity_feed.backend.handler import (
        ActivityFeedHandler,
    )
    from mozaiksai.core.auth import optional_user
    from mozaiksai.hosts.routers import modules as module_router

    templates = (
        Path(__file__).resolve().parents[1] / "factory_app/build_context/social/templates/modules"
    )
    modules = []
    handler = ActivityFeedHandler()
    stored = []

    async def insert(ctx, document):
        stored.append(dict(document))

    handler._service._repo = SimpleNamespace(insert=insert)
    for name in ("activity_feed", "friends", "user_posts"):
        root = templates / name
        definition = ModuleDefinition.model_validate(
            yaml.safe_load((root / "module.yaml").read_text(encoding="utf-8"))
        )
        manifests = {}
        for kind in ("events", "reactions"):
            path = root / f"contracts/{kind}.yaml"
            if path.exists():
                manifests[kind] = yaml.safe_load(path.read_text(encoding="utf-8"))
        modules.append(
            LoadedModule(
                definition,
                handler if name == "activity_feed" else SimpleNamespace(),
                root,
                ModuleCompanionManifests.model_validate(manifests),
            )
        )

    dispatcher = UnifiedEventDispatcher()
    emitted = []

    async def observe(envelope):
        emitted.append(envelope)

    dispatcher.register_handler("domain.social.activity.recorded", observe)
    ModuleEventRouter(modules, event_emitter=dispatcher.emit).register(dispatcher)
    incoming = source()
    incoming.update(type=event_type, payload=payload)
    incoming["source"]["module_id"] = (
        "friends" if action_id == "on_friendship_created" else "user_posts"
    )
    receipt = await dispatcher.emit(event_type, incoming)
    assert receipt.success and len(stored) == 1 and len(emitted) == 1
    assert emitted[0]["source"]["module_id"] == "activity_feed"
    assert emitted[0]["source"]["action_id"] == action_id
    assert emitted[0]["payload"]["event_id"] == stored[0]["event_id"]
    assert emitted[0]["authority"]["permissions"] == []

    app = FastAPI()
    app.include_router(module_router.router)
    app.state.module_action_surfaces = {
        "activity_feed": {action.id: action.api_surface for action in modules[0].definition.actions}
    }
    app.dependency_overrides[optional_user] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(f"/api/modules/activity_feed/{action_id}", json=payload)
    assert denied.status_code == 404 and denied.json()["detail"] == "Action not found"
    assert len(stored) == 1 and len(emitted) == 1


async def test_rejected_reaction_keeps_committed_write_but_not_durable_completion(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient

    from mozaiksai.core.runtime.composition.platform_hooks import (
        PlatformExtensionBundle,
        PlatformHookRegistry,
    )
    from mozaiksai.core.runtime.composition.reaction_idempotency_store import (
        ReactionIdempotencyStore,
    )

    audits = []

    async def capture(audit):
        audits.append(audit)

    hooks = PlatformHookRegistry()
    hooks._register_bundle(PlatformExtensionBundle(module_reaction_audit=capture))
    monkeypatch.setattr(PlatformHookRegistry, "_instance", hooks)

    uri = os.environ.get("MONGO_URI")
    if not uri:
        if os.environ.get("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("MONGO_URI is required")
        pytest.skip("MONGO_URI is not set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=1000)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        if os.environ.get("MOZAIKS_REQUIRE_REAL_MONGO"):
            raise
        pytest.skip("MongoDB is unavailable")
    database = f"reaction_emission_{uuid4().hex}"
    try:
        effects = client[database]["effects"]
        replies = []

        async def on_order(ctx, order_id):
            await effects.insert_one({"order_id": order_id})
            replies.append(await ctx.emit(OUTPUT, {"record_id": 123}))
            return ModuleReactionEffectAck(order_id)

        loaded = module(SimpleNamespace(on_order=on_order))
        loaded.manifests.reactions.reactions[0].idempotency_key = "order_id"
        store = ReactionIdempotencyStore(client=client, database_name=database)
        dispatcher = UnifiedEventDispatcher()
        runtime = ModuleEventRouter(
            [loaded], event_emitter=dispatcher.emit, idempotency_store=store
        )
        envelope = source()
        envelope["payload"] = {"order_id": "committed-order"}
        outcome = await runtime.handle_event(SOURCE, envelope)
        [delivery] = outcome.reactions
        assert delivery.status == "failed" and not delivery.success and delivery.effect_id is None
        assert (
            audits[0].outcome == "failed"
            and audits[0].reason == "event rejected: value_invalid"
            and delivery.audit_id == audits[0].reaction_dispatch_id
        )
        assert isinstance(replies[0], ModuleEventRejection)
        assert await effects.count_documents({"order_id": "committed-order"}) == 1
        [record] = await store._collection().find({}).to_list(None)
        assert record["status"] == "retryable" and not record.get("effect_id")
        assert await store.completion(
            app_id="app-a",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            idempotency_key_str=record["idempotency_key_str"],
        ) == (False, None)
    finally:
        await client.drop_database(database)
        client.close()
