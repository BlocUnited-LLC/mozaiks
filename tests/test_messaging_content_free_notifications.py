"""Messaging notifications carry no message text, from the domain event to the client API.

A rendered Messaging pack is loaded the way a host loads it and dispatched over
signed HTTP. Its ``domain.messages.message_sent`` event goes through the real
runtime dispatcher to the real ``ModuleEventRouter``, which applies the pack's
``notifications.yaml`` and writes through its default platform notification
writer. Only the Mongo transport, the JWKS lookup, and audit sinks are fakes.
"""
from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
import yaml
from bson import ObjectId
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.account import AccountDataRegistry
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
from mozaiksai.core.auth.config import clear_auth_config_cache
from mozaiksai.core.events.unified_event_dispatcher import UnifiedEventDispatcher
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal
from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceContext
from mozaiksai.hosts.routers import modules as module_router
from mozaiksai.hosts.routers import notifications as notification_router
from tests.test_messaging_pack_persistence_composition import (
    MIGRATION,
    PACK,
    _assembled_files,
    _DocumentMongoClient,
)

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "content-free-messaging-app"
DATABASE = "content_free_messaging_test"
AUDIENCE = "content-free-messaging-test"
MARKER = "ZQX-MARKER-7f3c1e-private-text"
CONTENT_FREE_TITLE = "New message"
CONTENT_FREE_BODY = "Open the conversation to read it."
MISSING = object()


# ---------------------------------------------------------------------------
# Platform notification store: the Mongo operators the notification routes use
# ---------------------------------------------------------------------------

def _get(row: dict[str, Any], path: str) -> Any:
    value: Any = row
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def _field_matches(actual: Any, condition: Any) -> bool:
    if isinstance(condition, dict) and condition and all(key.startswith("$") for key in condition):
        for operator, operand in condition.items():
            if operator == "$exists":
                if (actual is not MISSING) is not bool(operand):
                    return False
            elif operator == "$regex":
                if not (isinstance(actual, str) and re.search(operand, actual)):
                    return False
            elif operator == "$in":
                values = actual if isinstance(actual, list) else [actual]
                if not any(value in operand for value in values):
                    return False
            else:
                raise NotImplementedError(f"fake platform store does not model {operator}")
        return True
    if isinstance(actual, list) and not isinstance(condition, list):
        return condition in actual
    return actual is not MISSING and actual == condition


def _matches(row: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, condition in query.items():
        if key == "$and":
            matched = all(_matches(row, part) for part in condition)
        elif key == "$or":
            matched = any(_matches(row, part) for part in condition)
        elif key == "$nor":
            matched = not any(_matches(row, part) for part in condition)
        elif key == "$expr":
            ((operator, (field, value)),) = condition.items()
            if operator != "$eq" or not field.startswith("$"):
                raise NotImplementedError(f"fake platform store does not model $expr {condition}")
            matched = _get(row, field[1:]) == value
        elif key.startswith("$"):
            raise NotImplementedError(f"fake platform store does not model {key}")
        else:
            matched = _field_matches(_get(row, key), condition)
        if not matched:
            return False
    return True


class _NotificationCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def sort(self, field: str, direction: int) -> _NotificationCursor:
        self.rows.sort(key=lambda row: str(row.get(field) or ""), reverse=direction < 0)
        return self

    def limit(self, count: int) -> _NotificationCursor:
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self.rows[:length]


class _NotificationCollection:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any]) -> SimpleNamespace:
        # Motor stamps the caller's document with _id, as the real writer sees.
        document.setdefault("_id", ObjectId())
        self.rows.append(deepcopy(document))
        return SimpleNamespace(acknowledged=True, inserted_id=document["_id"])

    def find(self, query: dict[str, Any], projection: dict[str, int]) -> _NotificationCursor:
        if any(value != 0 for value in projection.values()):
            raise NotImplementedError("fake platform store models exclusion projections only")
        return _NotificationCursor([
            {key: deepcopy(value) for key, value in row.items() if key not in projection}
            for row in self.rows if _matches(row, query)
        ])

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for row in self.rows if _matches(row, query))

    async def delete_many(self, query: dict[str, Any]) -> SimpleNamespace:
        doomed = [row for row in self.rows if _matches(row, query)]
        self.rows = [row for row in self.rows if not any(row is target for target in doomed)]
        return SimpleNamespace(deleted_count=len(doomed))


class _PlatformMongo:
    def __init__(self) -> None:
        self.notifications = _NotificationCollection()

    def __getitem__(self, name: str) -> dict[str, _NotificationCollection]:
        assert name == "mozaiks"
        return {"platform_notifications": self.notifications}


def _text(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


# ---------------------------------------------------------------------------
# Host composition: rendered pack, signed HTTP, real dispatcher and router
# ---------------------------------------------------------------------------

@pytest.fixture
def host(tmp_path, monkeypatch):
    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in {"ENV": "test", "ENVIRONMENT": "test", "AUTH_ENABLED": "true",
                        "AUTH_PROVIDER": "jwt", "AUTH_AUDIENCE": AUDIENCE}.items():
        monkeypatch.setenv(name, value)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": AUDIENCE, "alg": "RS256", "use": "sig"})
    adapter = GenericJWTAdapter(config=JWTAdapterConfig(
        jwks_url="https://auth.test/jwks", issuer="https://auth.test", audience=AUDIENCE,
    ))
    monkeypatch.setattr(adapter, "_get_jwks_client_async", AsyncMock(return_value=SimpleNamespace(
        get_signing_key=AsyncMock(return_value=jwk),
    )))
    monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", lambda: adapter)
    monkeypatch.setattr(module_router, "record_action_invocation", lambda **_kwargs: None)
    hooks = PlatformHookRegistry()
    for target in (module_router, notification_router):
        monkeypatch.setattr(target, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr("mozaiksai.core.runtime.composition.module_executor.get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(ModuleExecutor, "_emit_dispatch_audit", AsyncMock())
    platform = _PlatformMongo()
    # The host passes no notification_store, so the router's default writer
    # and the notification routes share this platform client.
    monkeypatch.setattr("mozaiksai.core.core_config.get_mongo_client", lambda: platform)

    files = _assembled_files()
    app_root = tmp_path / "app"
    for filename, content in files.items():
        if filename.startswith("modules/messages/"):
            path = app_root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    accounts = AccountDataRegistry()
    monkeypatch.setattr("mozaiksai.core.account.account_data_registry", accounts)
    loaded = ModuleLoader(str(app_root)).load("messages")
    assert accounts.registered_module_ids() == ["messages"]

    dispatcher = UnifiedEventDispatcher()
    events: list[tuple[str, dict[str, Any]]] = []
    for event_type in ("domain.messages.thread_created", "domain.messages.message_sent",
                       "notification.created", "notification.count_changed"):
        dispatcher.register_handler(event_type, lambda payload, event_type=event_type: events.append(
            (event_type, deepcopy(payload))))
    router = ModuleEventRouter([loaded], event_emitter=dispatcher.emit)
    router.register(dispatcher)

    client = _DocumentMongoClient()
    contract = json.loads(files["data/contract.json"])
    executor = ModuleExecutor(event_emitter=dispatcher.emit, persistence_client=client,
                              persistence_database=DATABASE, data_contract=contract)
    executor.register_loaded_module(loaded)
    app = FastAPI()
    app.state.loaded_app_id = APP_ID
    app.state.module_action_surfaces = {"messages": loaded.action_api_surface_map}
    app.state.executor_registry = SimpleNamespace(module_executor=executor)
    app.include_router(module_router.router)
    app.include_router(notification_router.router)
    http = TestClient(app, raise_server_exceptions=False)

    def token(user: str) -> dict[str, str]:
        claims = {"sub": user, "iss": "https://auth.test", "aud": AUDIENCE, "app_id": APP_ID,
                  "scp": "messages.read messages.write", "exp": int(time.time()) + 300}
        return {"Authorization": "Bearer " + jwt.encode(
            claims, key, algorithm="RS256", headers={"kid": AUDIENCE, "typ": "at+jwt"},
        )}

    def action(user: str, name: str, **params: Any) -> dict[str, Any]:
        response = http.post(f"/api/modules/messages/{name}", json={"params": params}, headers=token(user))
        assert response.status_code == 200, response.text
        return response.json()

    def thread(user: str, *peers: str) -> str:
        created = action(user, "create_thread", participant_ids=list(peers),
                         thread_type="direct" if len(peers) == 1 else "group")
        assert created["success"] is True, created
        return created["thread"]["thread_id"]

    def send(user: str, thread_id: str, body: str) -> dict[str, Any]:
        sent = action(user, "send_message", thread_id=thread_id, body=body)
        assert sent["success"] is True, sent
        return sent["message"]

    def listed(user: str) -> dict[str, Any]:
        response = http.get("/api/notifications", headers=token(user))
        assert response.status_code == 200, response.text
        return response.json()

    def persistence(user: str) -> MongoPersistenceContext:
        return MongoPersistenceContext(app_id=APP_ID, user_id=user, database_name=DATABASE, client=client,
                                       data_contract=contract, principal=PersistencePrincipal(user_id=user))

    def messages() -> list[dict[str, Any]]:
        return client[DATABASE][persistence("probe").collection_name("messages", "messages")].rows

    async def delete_account(user: str) -> dict[str, Any]:
        # DELETE /api/account calls the registry with the account's persistence.
        return await accounts.delete_all(app_id=APP_ID, user_id=user, db=None, persistence=persistence(user))

    yield SimpleNamespace(
        files=files, http=http, token=token, action=action, thread=thread, send=send, listed=listed,
        events=events, notifications=platform.notifications, messages=messages, delete_account=delete_account,
        app_root=app_root,
    )
    router.unregister()
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()


def _of(events: list[tuple[str, dict[str, Any]]], event_type: str) -> list[dict[str, Any]]:
    return [payload for kind, payload in events if kind == event_type]


# ---------------------------------------------------------------------------
# Pack contract and identity
# ---------------------------------------------------------------------------

def test_messaging_notification_contract_is_content_free_under_a_new_pack_version():
    """The text change ships as a new pack identity, never as relabelled 0.1.13 content."""
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        verify_pack_integrity,
    )

    identity = verify_pack_integrity(PACK, "messaging")
    assert _version(identity["version"]) >= (0, 1, 14)
    for source in (
        PACK / "templates/modules/messages/contracts/notifications.yaml",
        ROOT / "factory_app/app/modules/messages/contracts/notifications.yaml",
    ):
        (rule,) = yaml.safe_load(source.read_text(encoding="utf-8"))["notifications"]
        assert rule["event_type"] == "domain.messages.message_sent"
        assert rule["template"] == {"title": CONTENT_FREE_TITLE, "body": CONTENT_FREE_BODY}, source
        assert "{{" not in _text(rule["template"])
        assert set(rule["context_fields"]) == {"thread_id", "message_id", "related_type", "related_id"}


# ---------------------------------------------------------------------------
# Event -> notification -> client, authorization, deletion, isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_notification_reaches_recipient_without_the_message_text(host):
    thread_id = host.thread("alice", "bob")
    message = host.send("alice", thread_id, f"meet at {MARKER}")

    # (a) The intended recipient, and only that recipient, is notified.
    (record,) = host.notifications.rows
    assert (record["rule_id"], record["module_id"], record["app_id"]) == ("message_sent", "messages", APP_ID)
    assert record["audience"] == {
        "user_id_field": "recipient_ids", "user_ids": ["bob"], "roles": [], "permissions": [],
    }
    assert (record["title"], record["body"]) == (CONTENT_FREE_TITLE, CONTENT_FREE_BODY)
    assert record["context"] == {
        "thread_id": thread_id, "message_id": message["message_id"], "related_type": None, "related_id": None,
    }

    # A plain direct thread has no subject app or related record; its lifecycle events still dispatch.
    (created_thread,) = _of(host.events, "domain.messages.thread_created")
    assert (created_thread["payload"]["related_type"], created_thread["payload"]["related_id"]) == (None, None)
    # The original domain event is delivered unchanged; only the notification projection is content-free.
    (domain_event,) = _of(host.events, "domain.messages.message_sent")
    assert domain_event["payload"]["body_preview"] == f"meet at {MARKER}"
    assert record["source_event_id"] == domain_event["id"]
    assert record["source_event"] == {"id": domain_event["id"], "type": "domain.messages.message_sent"}
    assert [row["body"] for row in host.messages()] == [f"meet at {MARKER}"]

    # (b) The marker is absent from the whole persisted record and every delivered notification.
    assert MARKER not in _text(record)
    (created,) = _of(host.events, "notification.created")
    assert created["payload"]["notification_id"] == record["notification_id"]
    # Exactly the fields the CHANGELOG and docs list; the saved copy also gets MongoDB's _id.
    assert set(created["payload"]) == {
        "notification_id", "rule_id", "module_id", "event_type", "source_event_id", "app_id", "actor",
        "audience", "channels", "title", "body", "status", "created_at", "source_event", "context",
    }
    assert set(record) == {*created["payload"], "_id"} and record["status"] == "unread"
    assert MARKER not in _text(created)
    assert MARKER not in _text(_of(host.events, "notification.count_changed"))
    bob_view = host.listed("bob")
    assert [row["notification_id"] for row in bob_view["notifications"]] == [record["notification_id"]]
    assert MARKER not in _text(bob_view)
    assert host.listed("alice")["notifications"] == []

    # (c) The notification is a pointer: opening it applies current authorization.
    opened = host.action("bob", "get_thread", thread_id=record["context"]["thread_id"])
    assert [row["body"] for row in opened["messages"]] == [f"meet at {MARKER}"]
    carol = host.action("carol", "get_thread", thread_id=record["context"]["thread_id"])
    assert (carol["thread"], carol["messages"]) == (None, [])
    assert MARKER not in _text(carol)
    injected = host.action("carol", "send_message", thread_id=record["context"]["thread_id"], body="hi")
    assert injected["success"] is False
    assert host.listed("carol")["notifications"] == []
    assert len(host.notifications.rows) == 1


@pytest.mark.asyncio
async def test_deleted_sender_content_does_not_resurface_and_unrelated_notifications_survive(host):
    deleted_thread = host.thread("alice", "bob")
    host.send("alice", deleted_thread, f"first {MARKER}")
    kept_thread = host.thread("carol", "bob")
    host.send("carol", kept_thread, "carol stays")
    other_thread = host.thread("dave", "carol")
    host.send("dave", other_thread, "dave stays")
    before = deepcopy(host.notifications.rows)
    assert len(before) == 3

    # (d) The sender's account deletion removes the message; the notification exposes nothing.
    result = await host.delete_account("alice")
    assert result["messages"]["messages_deleted"] == 1
    assert MARKER not in _text(host.messages())
    opened = host.action("bob", "get_thread", thread_id=deleted_thread)
    assert MARKER not in _text(opened)
    assert opened["messages"] == []
    assert MARKER not in _text(host.notifications.rows)
    assert MARKER not in _text(host.listed("bob"))

    # (e) Other recipients, threads and notifications are neither deleted nor changed.
    assert host.notifications.rows == before
    assert {row["audience"]["user_ids"][0] for row in host.notifications.rows} == {"bob", "carol"}
    assert [row["body"] for row in host.action("bob", "get_thread", thread_id=kept_thread)["messages"]] == [
        "carol stays"]
    assert [row["body"] for row in host.action("carol", "get_thread", thread_id=other_thread)["messages"]] == [
        "dave stays"]


# ---------------------------------------------------------------------------
# Records written under the 0.1.13 preview contract
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pre_0_1_14_message_notification_keeps_copied_text_until_its_recipient_clears_it(host):
    """Documented limitation: no supported upgrade or account path rewrites old notifications.

    A record written by the 0.1.13 template and the earlier router keeps the
    preview in ``body`` and the whole event in ``source_event``. The pack
    upgrade, both account deletions, and the new router leave it as stored;
    the platform has no notification retention. Only the recipient's own
    ``DELETE /api/notifications`` removes it. The owner that could correct
    this is the platform account deletion route, which runs no
    platform_notifications step; module account handlers are not given that
    store.
    """
    thread_id = host.thread("alice", "bob")
    kept = host.thread("carol", "bob")
    host.send("carol", kept, "carol stays")
    (current,) = deepcopy(host.notifications.rows)
    legacy_event = {
        "id": "evt_legacy_preview", "type": "domain.messages.message_sent", "version": 1,
        "tenant": {"app_id": APP_ID, "tenant_id": None},
        "actor": {"type": "user", "id": "alice"},
        "payload": {"thread_id": thread_id, "message_id": "msg_legacy", "sender_id": "alice",
                    "sender_role": "user", "body_preview": MARKER, "recipient_ids": ["bob"]},
    }
    earlier_record = {
        **{key: current[key] for key in ("rule_id", "module_id", "event_type", "app_id", "audience", "channels")},
        "notification_id": "ntf_legacy_preview", "source_event_id": legacy_event["id"],
        "actor": legacy_event["actor"], "title": "New message", "body": MARKER, "status": "unread",
        "created_at": "2026-10-01T00:00:00+00:00", "context": {"thread_id": thread_id},
        "source_event": legacy_event,
    }
    await host.notifications.insert_one(deepcopy(earlier_record))
    stored_legacy = deepcopy(host.notifications.rows[-1])

    # Pack upgrade: the 0.1.14 migration touches only Messaging collections, and a
    # restarted host loads the new rules without rewriting stored notifications.
    operations = json.loads(host.files[MIGRATION])["operations"]
    assert {(op["type"], op["module_id"]) for op in operations} <= {
        ("ensure_collection", "messages"), ("ensure_index", "messages"),
    }
    restarted = ModuleEventRouter([ModuleLoader(str(host.app_root)).load("messages")])
    restarted.register(UnifiedEventDispatcher())
    # DELETE /api/account runs the registered account handlers and the host hook;
    # neither the route nor the Messaging handler reaches platform notifications.
    assert "platform_notifications" not in (ROOT / "mozaiksai/hosts/routers/account.py").read_text(encoding="utf-8")
    await host.delete_account("alice")
    await host.delete_account("bob")
    assert host.notifications.rows[-1] == stored_legacy
    assert MARKER in _text(host.notifications.rows)

    # The list route projects source_event out but still returns the old preview to the recipient.
    (row,) = [item for item in host.listed("bob")["notifications"]
              if item["notification_id"] == "ntf_legacy_preview"]
    assert row["body"] == MARKER and "source_event" not in row

    # The recipient's own clear is the only supported removal; it leaves other users' records.
    other = {"notification_id": "ntf_other_user", "app_id": APP_ID, "audience": {"user_ids": ["carol"]},
             "status": "unread", "created_at": "2026-10-02T00:00:00+00:00", "title": "Other"}
    await host.notifications.insert_one(deepcopy(other))
    cleared = host.http.delete("/api/notifications", headers=host.token("bob"))
    assert cleared.json() == {"success": True, "cleared_count": 2}
    assert [row["notification_id"] for row in host.notifications.rows] == ["ntf_other_user"]
    assert MARKER not in _text(host.notifications.rows)


def test_content_free_reference_keeps_support_notifications_workspace_scoped():
    """Dropping the payload must not turn a support reply into an app-wide notification."""
    import asyncio

    stored: list[dict[str, Any]] = []

    async def store(record):
        stored.append(deepcopy(record))

    # No context_fields: only the source reference can carry the support classification.
    rule = {"id": "reply", "module_id": "custom", "audience": {"user_id_field": "recipient_ids"},
            "source_event": "reference",
            "template": {"title": "Reply", "body": "Open the conversation to read it."}}
    asyncio.run(ModuleEventRouter([], notification_store=store)._create_notification(
        rule, "domain.custom.reply", {
            "id": "evt_support_reply", "type": "domain.custom.reply",
            "tenant": {"app_id": APP_ID, "workspace_id": "ws-a"},
            "payload": {"recipient_ids": ["bob"], "related_type": "workspace_support.request",
                        "body_preview": MARKER},
        },
    ))
    (record,) = stored
    assert MARKER not in _text(record)
    assert not _matches(record, notification_router._notification_workspace_filter(None))
    assert not _matches(record, notification_router._notification_workspace_filter("ws-b"))
    assert _matches(record, notification_router._notification_workspace_filter("ws-a"))


def test_factory_app_message_notification_is_content_free():
    """The factory app's own messages module renders the same content-free notification."""
    import asyncio

    stored: list[dict[str, Any]] = []
    emitted: list[dict[str, Any]] = []

    async def store(record):
        stored.append(deepcopy(record))

    async def emit(_event_type, payload):
        emitted.append(deepcopy(payload))

    router = ModuleEventRouter(
        [ModuleLoader(str(ROOT / "factory_app/app")).load("messages")], event_emitter=emit, notification_store=store,
    )
    asyncio.run(router.handle_event("domain.messages.message_sent", {
        "id": "evt_factory_reply", "type": "domain.messages.message_sent",
        "tenant": {"app_id": "app_1", "tenant_id": "tenant_1"},
        "payload": {"thread_id": "thr_1", "message_id": "msg_1", "sender_role": "operator",
                    "sender_id": "operator_1", "recipient_ids": ["ticket_owner"], "body_preview": MARKER,
                    "sent_at": "2026-01-01T00:00:00Z", "related_type": "workspace_support.request",
                    "related_id": "sr_1"},
    }))
    (record,) = stored
    assert (record["title"], record["body"]) == (CONTENT_FREE_TITLE, CONTENT_FREE_BODY)
    assert record["source_event"] == {
        "id": "evt_factory_reply", "type": "domain.messages.message_sent",
        "related_type": "workspace_support.request",
    }
    assert MARKER not in _text(record)
    assert MARKER not in _text(emitted)
