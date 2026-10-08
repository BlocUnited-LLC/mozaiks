"""Signed HTTP regression for workspace support notifications on real Mongo.

Run with MOZAIKS_RUN_REAL_MONGO_TESTS=1 and a local MONGO_URI. Only the JWKS
transport and host membership lookup are synthetic; Mongo executes the actual
notification queries and mutations.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
from mozaiksai.core.auth.config import clear_auth_config_cache
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.hosts.routers import notifications as notification_router

pytestmark = pytest.mark.skipif(
    os.getenv("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1",
    reason="set MOZAIKS_RUN_REAL_MONGO_TESTS=1 for signed notification HTTP/Mongo acceptance",
)

APP_ID = "notification-scope-app"
USER_ID = "same-user"
TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
WORKSPACE_A = "workspace-a"
WORKSPACE_B = "workspace-b"


def _record(
    notification_id: str, *, module_id: str, event_type: str,
    workspace_id: str | list[str] | None = None,
    related_type: str | None = None, ownerless: bool = False,
) -> dict:
    audience = {"permissions": ["workspace_support.read"]} if module_id == "workspace_support" else (
        {"user_ids": [USER_ID]} if module_id == "messages" else {}
    )
    record = {
        "notification_id": notification_id,
        "app_id": APP_ID,
        "module_id": module_id,
        "event_type": event_type,
        "audience": audience,
        "status": "unread",
        "title": notification_id,
        "body": f"secret body {notification_id}",
        "created_at": "2026-10-08T00:00:00+00:00",
    }
    if not ownerless:
        record["workspace_id"] = workspace_id
    if related_type:
        record["context"] = {"related_type": related_type}
    return record


def _role_record(
    notification_id: str, *, workspace_id: str | list[str] | None = None,
    tenant_id: str | list[str] | None = None, ownerless: bool = False,
    rule_id: str = "hosting_deployed.user",
) -> dict:
    record = _record(
        notification_id, module_id="hosting", event_type="hosted.hosting.app.deployed",
        workspace_id=workspace_id, ownerless=ownerless,
    )
    record["rule_id"] = rule_id
    record["audience"] = {"roles": ["owner"]}
    record["body"] = f"https://{notification_id}.example.invalid"
    if tenant_id is not None:
        record["tenant_id"] = tenant_id
    return record


@pytest.fixture
def notification_http(monkeypatch):
    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "ENV": "test", "ENVIRONMENT": "test", "AUTH_ENABLED": "true",
        "AUTH_PROVIDER": "jwt", "AUTH_ISSUER": "https://auth.test",
        "AUTH_AUDIENCE": "notification-scope", "AUTH_JWKS_URL": "https://auth.test/jwks",
    }.items():
        monkeypatch.setenv(name, value)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "notification-scope", "alg": "RS256", "use": "sig"})
    adapter = GenericJWTAdapter(config=JWTAdapterConfig(
        jwks_url="https://auth.test/jwks", issuer="https://auth.test", audience="notification-scope",
    ))
    monkeypatch.setattr(adapter, "_get_jwks_client_async", AsyncMock(return_value=SimpleNamespace(
        get_signing_key=AsyncMock(return_value=jwk),
    )))
    monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", lambda: adapter)

    def token(
        workspace_id: str | None, *, tenant_id: str | None = None, app_id: str | None = APP_ID,
        roles: list[str] | None = None,
    ) -> dict[str, str]:
        claims = {
            "sub": USER_ID, "iss": "https://auth.test", "aud": "notification-scope",
            "scp": "workspace_support.read", "exp": int(time.time()) + 300,
        }
        if app_id is not None:
            claims["app_id"] = app_id
        if workspace_id is not None:
            claims["workspace_id"] = workspace_id
        if tenant_id is not None:
            claims["tid"] = tenant_id
        if roles is not None:
            claims["roles"] = roles
        return {"Authorization": "Bearer " + jwt.encode(
            claims, key, algorithm="RS256", headers={"kid": "notification-scope", "typ": "at+jwt"},
        )}

    hooks = PlatformHookRegistry()

    def verified_membership(*, principal, requested_scope, **_kwargs):
        # The app tenant ID differs from Entra's tid. A token tid must not be
        # used as a membership selector for this host.
        if requested_scope.get("tenant_id"):
            return {"verified_tenant_id": None, "verified_workspace_id": None}
        requested = requested_scope.get("workspace_id")
        member_workspaces = {WORKSPACE_A, WORKSPACE_B} if principal.user_id == USER_ID else set()
        # Hosted find_one without a selector would choose an arbitrary active
        # membership. The notification router must not use that fallback.
        if requested is None:
            requested = WORKSPACE_A
        verified = requested if requested in member_workspaces else None
        return {
            "verified_tenant_id": (
                TENANT_A if verified == WORKSPACE_A else TENANT_B if verified == WORKSPACE_B else None
            ),
            "verified_workspace_id": verified,
        }

    hooks.register_bundle({"module_scope_resolver": verified_membership}, source="test-membership")
    monkeypatch.setattr(notification_router, "get_platform_hooks", lambda: hooks)

    uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    database_name = f"notification_scope_{uuid4().hex}"
    seed_client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    seed_client.admin.command("ping")
    collection = seed_client[database_name]["platform_notifications"]
    collection.insert_many([
        _record("support-a", module_id="workspace_support", event_type="domain.workspace_support.request_created",
                workspace_id=WORKSPACE_A),
        _record("support-b", module_id="workspace_support", event_type="domain.workspace_support.request_created",
                workspace_id=WORKSPACE_B),
        _record("support-ownerless", module_id="workspace_support", event_type="domain.workspace_support.request_created",
                ownerless=True),
        _record("support-array", module_id="workspace_support", event_type="domain.workspace_support.request_created",
                workspace_id=[WORKSPACE_A, WORKSPACE_B]),
        _record("reply-a", module_id="messages", event_type="domain.messages.message_sent",
                workspace_id=WORKSPACE_A, related_type="workspace_support.request"),
        _record("reply-b", module_id="messages", event_type="domain.messages.message_sent",
                workspace_id=WORKSPACE_B, related_type="workspace_support.request"),
        _record("reply-ownerless", module_id="messages", event_type="domain.messages.message_sent",
                related_type="workspace_support.request", ownerless=True),
        {
            **_record("reply-source-ownerless", module_id="messages",
                      event_type="domain.messages.message_sent", ownerless=True),
            "source_event": {"payload": {"related_type": "workspace_support.request"}},
        },
        _record("direct-message", module_id="messages", event_type="domain.messages.message_sent",
                workspace_id=WORKSPACE_B),
        _record("app-wide", module_id="billing", event_type="domain.billing.updated", ownerless=True),
    ])

    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)

    class _NotificationMongo:
        def __getitem__(self, name):
            assert name == "mozaiks"
            return mongo[database_name]

    monkeypatch.setattr("mozaiksai.core.core_config.get_mongo_client", _NotificationMongo)
    app = FastAPI()
    app.state.loaded_app_id = APP_ID
    app.include_router(notification_router.router)
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, token=token, collection=collection, hooks=hooks, app=app)
    finally:
        mongo.close()
        seed_client.drop_database(database_name)
        seed_client.close()
        clear_auth_config_cache()
        auth_registry.reset_auth_adapter()


def _ids(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {row["notification_id"] for row in response.json()["notifications"]}


def _seed_role_notifications(notification_http):
    records = [
        _role_record("role-tenant-a", ownerless=True, tenant_id=TENANT_A),
        _role_record("role-tenant-b", ownerless=True, tenant_id=TENANT_B),
        _role_record("role-workspace-a", workspace_id=WORKSPACE_A),
        _role_record("role-workspace-b", workspace_id=WORKSPACE_B),
        _role_record("role-ownerless", ownerless=True),
        _role_record("role-workspace-array", workspace_id=[WORKSPACE_A, WORKSPACE_B], tenant_id=TENANT_A),
        _role_record("role-tenant-array", workspace_id=WORKSPACE_A, tenant_id=[TENANT_A, TENANT_B]),
        _role_record("role-mismatched-owner", workspace_id=WORKSPACE_A, tenant_id=TENANT_B),
        {
            **_role_record("role-and-recipient-b", workspace_id=WORKSPACE_B, tenant_id=TENANT_B),
            "audience": {"roles": ["owner"], "user_ids": [USER_ID]},
        },
        {
            **_record("role-announcement-b", module_id="messages",
                      event_type="hosted.messages.announcement_created", workspace_id=WORKSPACE_B),
            "rule_id": "message_announcement_created",
            "tenant_id": TENANT_B,
            "audience": {"roles": ["owner", "investor", "admin"]},
            "body": "A hosted Mozaiks announcement is available.",
        },
    ]
    notification_http.collection.insert_many(records)

    async def store(record):
        record["notification_id"] = record["source_event"]["payload"]["probe_id"]
        notification_http.collection.insert_one(record)

    # This matches the hosted hosting_deployed.user rule's role audience and
    # rendered URL, while the OSS event router supplies trusted ownership.
    rule = {
        "id": "hosting_deployed.user", "module_id": "hosting",
        "audience": {"roles": ["owner"]},
        "template": {
            "title": "Your app is live",
            "body": "Your app is now hosted and accessible at {{hosting_url}}.",
        },
    }
    event_type = "hosted.hosting.app.deployed"
    router = ModuleEventRouter([], notification_store=store)

    async def produce():
        for notification_id, tenant_id, workspace_id in (
            ("role-a", TENANT_A, WORKSPACE_A),
            ("role-b", TENANT_B, WORKSPACE_B),
        ):
            await router._create_notification(rule, event_type, {
                "id": f"event-{notification_id}",
                "type": event_type,
                "tenant": {"app_id": APP_ID, "tenant_id": tenant_id, "workspace_id": workspace_id},
                "payload": {
                    "probe_id": notification_id,
                    "owner_id": USER_ID,
                    "hosting_url": f"https://{notification_id}.example.invalid",
                },
            })

    asyncio.run(produce())


def test_owned_role_alerts_require_exact_verified_workspace_and_tenant(notification_http):
    _seed_role_notifications(notification_http)
    client = notification_http.client
    a = notification_http.token(WORKSPACE_A, roles=["owner"])
    b = notification_http.token(WORKSPACE_B, roles=["owner"])
    expected_a = {
        "support-a", "reply-a", "direct-message", "app-wide",
        "role-a", "role-tenant-a", "role-workspace-a",
    }
    expected_b = {
        "support-b", "reply-b", "direct-message", "app-wide", "role-announcement-b",
        "role-b", "role-tenant-b", "role-workspace-b", "role-and-recipient-b",
    }
    assert _ids(client.get("/api/notifications", headers=a)) == expected_a
    assert _ids(client.get("/api/notifications", headers=b)) == expected_b
    assert _ids(client.get("/api/notifications", headers=notification_http.token(
        WORKSPACE_A, tenant_id="entra-directory", roles=["owner"],
    ))) == expected_a
    assert _ids(client.get("/api/notifications", headers=notification_http.token(WORKSPACE_A))) == {
        "support-a", "reply-a", "direct-message", "app-wide",
    }
    assert client.get("/api/notifications/count", headers=a).json() == {"count": 7, "unread_count": 7}
    assert client.get("/api/notifications/count", headers=b).json() == {"count": 9, "unread_count": 9}


def test_owned_role_alert_mutations_deny_foreign_and_ambiguous_owners(notification_http):
    _seed_role_notifications(notification_http)
    client = notification_http.client
    a = notification_http.token(WORKSPACE_A, roles=["owner"])
    foreign = {
        "role-b", "role-tenant-b", "role-workspace-b", "role-and-recipient-b", "role-announcement-b",
        "role-ownerless", "role-workspace-array", "role-tenant-array", "role-mismatched-owner",
    }
    for notification_id in foreign:
        assert client.post(f"/api/notifications/{notification_id}/read", headers=a).json()["success"] is False
    assert client.post("/api/notifications/role-a/read", headers=a).json()["success"] is True
    assert client.post("/api/notifications/mark-all-read", headers=a).json()["marked_count"] == 6
    assert notification_http.collection.count_documents({"notification_id": {"$in": list(foreign)},
                                                         "status": "unread"}) == len(foreign)
    assert client.delete("/api/notifications", headers=a).json()["cleared_count"] == 7
    assert notification_http.collection.count_documents({"notification_id": {"$in": list(foreign)}}) == len(foreign)


def test_unbound_owner_role_cannot_read_or_mutate_owned_alerts(notification_http):
    _seed_role_notifications(notification_http)
    client = notification_http.client
    unbound = notification_http.token(None, roles=["owner"])
    assert _ids(client.get("/api/notifications", headers=unbound)) == {
        "direct-message", "app-wide",
    }
    assert client.get("/api/notifications/count", headers=unbound).json() == {"count": 2, "unread_count": 2}
    assert client.post("/api/notifications/role-a/read", headers=unbound).json()["success"] is False
    assert client.post("/api/notifications/role-b/read", headers=unbound).json()["success"] is False
    assert client.post("/api/notifications/mark-all-read", headers=unbound).json()["marked_count"] == 2
    assert client.delete("/api/notifications", headers=unbound).json()["cleared_count"] == 2
    assert notification_http.collection.count_documents({"module_id": "hosting", "status": "unread"}) == 11


def test_tenant_owned_role_alert_requires_verified_tenant(notification_http, monkeypatch):
    _seed_role_notifications(notification_http)
    hooks = PlatformHookRegistry()
    hooks.register_bundle({
        "module_scope_resolver": lambda **_kwargs: {"verified_workspace_id": WORKSPACE_A},
    }, source="workspace-only-membership")
    monkeypatch.setattr(notification_router, "get_platform_hooks", lambda: hooks)
    a = notification_http.token(WORKSPACE_A, roles=["owner"])
    assert _ids(notification_http.client.get("/api/notifications", headers=a)) == {
        "support-a", "reply-a", "direct-message", "app-wide", "role-workspace-a",
    }
    assert notification_http.client.post("/api/notifications/role-a/read", headers=a).json()["success"] is False
    assert notification_http.client.post("/api/notifications/role-workspace-a/read", headers=a).json()["success"] is True


def test_same_user_workspace_switch_scopes_support_alerts(notification_http):
    client = notification_http.client
    a = notification_http.token(WORKSPACE_A)
    b = notification_http.token(WORKSPACE_B)
    expected_a = {"support-a", "reply-a", "direct-message", "app-wide"}
    expected_b = {"support-b", "reply-b", "direct-message", "app-wide"}

    assert _ids(client.get("/api/notifications", headers=a)) == expected_a
    assert _ids(client.get("/api/notifications?workspace_id=workspace-b", headers=a)) == expected_a
    assert _ids(client.get("/api/notifications", headers=b)) == expected_b
    assert _ids(client.get("/api/notifications", headers=notification_http.token(None))) == {
        "direct-message", "app-wide",
    }
    assert _ids(client.get("/api/notifications", headers=notification_http.token(
        WORKSPACE_A, tenant_id="entra-directory",
    ))) == expected_a
    assert client.get("/api/notifications/count", headers=a).json() == {"count": 4, "unread_count": 4}
    assert client.get("/api/notifications/count", headers=b).json() == {"count": 4, "unread_count": 4}
    assert _ids(client.get("/api/notifications", headers=notification_http.token("not-a-membership"))) == {
        "direct-message", "app-wide",
    }


def test_support_notification_mutations_require_verified_workspace(notification_http):
    client = notification_http.client
    a = notification_http.token(WORKSPACE_A)
    b = notification_http.token(WORKSPACE_B)
    for foreign_id in (
        "support-b", "reply-b", "support-ownerless", "support-array", "reply-ownerless", "reply-source-ownerless",
    ):
        response = client.post(f"/api/notifications/{foreign_id}/read", headers=a)
        assert response.status_code == 200, response.text
        assert response.json()["success"] is False

    assert client.post("/api/notifications/support-a/read", headers=a).json()["success"] is True
    assert client.post("/api/notifications/mark-all-read", headers=a).json()["marked_count"] == 3
    assert notification_http.collection.count_documents({"status": "unread"}) == 6
    assert client.delete("/api/notifications", headers=a).json()["cleared_count"] == 4
    assert {row["notification_id"] for row in notification_http.collection.find({})} == {
        "support-b", "reply-b", "support-ownerless", "support-array", "reply-ownerless", "reply-source-ownerless",
    }

    assert client.post("/api/notifications/support-b/read", headers=b).json()["success"] is True
    assert client.post("/api/notifications/mark-all-read", headers=b).json()["marked_count"] == 1
    assert client.delete("/api/notifications", headers=b).json()["cleared_count"] == 2
    assert {row["notification_id"] for row in notification_http.collection.find({})} == {
        "support-ownerless", "support-array", "reply-ownerless", "reply-source-ownerless",
    }


def test_unbound_token_cannot_mutate_support_alerts(notification_http):
    client = notification_http.client
    unbound = notification_http.token(None)
    for notification_id in (
        "support-a", "support-b", "reply-a", "reply-b", "support-ownerless", "support-array",
        "reply-ownerless", "reply-source-ownerless",
    ):
        assert client.post(f"/api/notifications/{notification_id}/read", headers=unbound).json()["success"] is False
    assert client.get("/api/notifications/count", headers=unbound).json() == {"count": 2, "unread_count": 2}
    assert client.post("/api/notifications/mark-all-read", headers=unbound).json()["marked_count"] == 2
    assert client.delete("/api/notifications", headers=unbound).json()["cleared_count"] == 2
    assert {row["notification_id"] for row in notification_http.collection.find({})} == {
        "support-a", "support-b", "reply-a", "reply-b", "support-ownerless", "support-array",
        "reply-ownerless", "reply-source-ownerless",
    }


def test_host_assertion_must_match_token_workspace(notification_http):
    notification_http.hooks.register_bundle({
        "module_scope_resolver": lambda **_kwargs: {"verified_workspace_id": WORKSPACE_B},
    }, source="mismatched-membership")
    a = notification_http.token(WORKSPACE_A)
    assert _ids(notification_http.client.get("/api/notifications", headers=a)) == {
        "direct-message", "app-wide",
    }
    assert notification_http.client.post("/api/notifications/support-a/read", headers=a).json()["success"] is False


def test_host_lookup_failure_keeps_app_wide_alerts(notification_http):
    def unavailable(**_kwargs):
        raise RuntimeError("membership store unavailable")

    notification_http.hooks.register_bundle({"module_scope_resolver": unavailable}, source="failed-membership")
    a = notification_http.token(WORKSPACE_A)
    assert _ids(notification_http.client.get("/api/notifications", headers=a)) == {
        "direct-message", "app-wide",
    }
    assert notification_http.client.get("/api/notifications/count", headers=a).json() == {
        "count": 2, "unread_count": 2,
    }
    assert notification_http.client.post("/api/notifications/support-a/read", headers=a).json()["success"] is False


def test_token_bound_app_cannot_override_notification_app_scope(notification_http):
    notification_http.collection.insert_one({
        **_record("other-app", module_id="workspace_support",
                  event_type="domain.workspace_support.request_created", workspace_id=WORKSPACE_A),
        "app_id": "other-app",
    })
    a = notification_http.token(WORKSPACE_A)
    assert notification_http.client.get("/api/notifications?app_id=other-app", headers=a).status_code == 403
    assert notification_http.client.post("/api/notifications/mark-all-read?app_id=other-app", headers=a).status_code == 403
    assert notification_http.client.delete("/api/notifications?app_id=other-app", headers=a).status_code == 403
    assert notification_http.client.post("/api/notifications/other-app/read", headers=a).json()["success"] is False
    assert notification_http.collection.find_one({"notification_id": "other-app"})["status"] == "unread"


def test_claimless_token_is_confined_to_loaded_app_for_reads_and_mutations(notification_http):
    foreign = [
        {**_record("foreign-support", module_id="workspace_support",
                   event_type="domain.workspace_support.request_created", workspace_id=WORKSPACE_A),
         "app_id": "other-app"},
        {**_record("foreign-message", module_id="messages",
                   event_type="domain.messages.message_sent", ownerless=True),
         "app_id": "other-app"},
        {**_record("foreign-global", module_id="billing",
                   event_type="domain.billing.updated", ownerless=True),
         "app_id": "other-app"},
    ]
    notification_http.collection.insert_many(foreign)
    client = notification_http.client
    claimless = notification_http.token(WORKSPACE_A, app_id=None)
    assert _ids(client.get("/api/notifications", headers=claimless)) == {
        "support-a", "reply-a", "direct-message", "app-wide",
    }
    assert client.get("/api/notifications/count", headers=claimless).json() == {"count": 4, "unread_count": 4}
    for notification_id in ("foreign-support", "foreign-message", "foreign-global"):
        assert client.post(f"/api/notifications/{notification_id}/read", headers=claimless).json()["success"] is False
    assert client.post("/api/notifications/mark-all-read", headers=claimless).json()["marked_count"] == 4
    assert client.delete("/api/notifications", headers=claimless).json()["cleared_count"] == 4
    assert {row["notification_id"] for row in notification_http.collection.find({"app_id": "other-app"})} == {
        "foreign-support", "foreign-message", "foreign-global",
    }
    assert notification_http.collection.count_documents({"app_id": "other-app", "status": "unread"}) == 3
    assert client.get("/api/notifications?app_id=other-app", headers=claimless).status_code == 403
    assert client.post("/api/notifications/mark-all-read?app_id=other-app", headers=claimless).status_code == 403
    assert client.delete("/api/notifications?app_id=other-app", headers=claimless).status_code == 403


def test_token_app_mismatch_is_rejected_before_notification_access(notification_http):
    client = notification_http.client
    foreign_claim = notification_http.token(WORKSPACE_A, app_id="other-app")
    assert client.get("/api/notifications/count", headers=foreign_claim).status_code == 403
    assert client.get("/api/notifications", headers=foreign_claim).status_code == 403
    assert client.post("/api/notifications/support-a/read", headers=foreign_claim).status_code == 403
    assert client.post("/api/notifications/mark-all-read", headers=foreign_claim).status_code == 403
    assert client.delete("/api/notifications", headers=foreign_claim).status_code == 403
    assert notification_http.collection.count_documents({}) == 10


def test_missing_loaded_app_identity_fails_closed_before_mongo(notification_http):
    notification_http.app.state.loaded_app_id = None
    client = notification_http.client
    a = notification_http.token(WORKSPACE_A)
    assert client.get("/api/notifications/count", headers=a).status_code == 503
    assert client.get("/api/notifications", headers=a).status_code == 503
    assert client.post("/api/notifications/support-a/read", headers=a).status_code == 503
    assert client.post("/api/notifications/mark-all-read", headers=a).status_code == 503
    assert client.delete("/api/notifications", headers=a).status_code == 503
    assert notification_http.collection.count_documents({}) == 10
