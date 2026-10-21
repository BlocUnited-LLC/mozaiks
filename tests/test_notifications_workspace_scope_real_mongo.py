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
    audience = {"permissions": ["workspace_support.read"], "user_ids": [USER_ID]} if module_id == "workspace_support" else (
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
        roles: list[str] | None = None, user_id: str = USER_ID,
    ) -> dict[str, str]:
        claims = {
            "sub": user_id, "iss": "https://auth.test", "aud": "notification-scope",
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
                ownerless=True),
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


def _register_notification_memberships(notification_http, monkeypatch, memberships):
    """Synthetic host lookup; JWT validation, HTTP and Mongo queries are real."""
    hooks = PlatformHookRegistry()

    async def current_membership(*, principal, app_id):
        candidates = [row for row in memberships if row["user_id"] == principal.user_id
                      and row["status"] == "active"
                      and (not principal.workspace_id
                           or row["workspace_id"] == principal.workspace_id)]
        if len(candidates) != 1:
            return None
        row = candidates[0]
        return {
            "app_id": app_id, "user_id": row["user_id"],
            "tenant_id": row["tenant_id"], "workspace_id": row["workspace_id"],
            "roles": row["roles"], "permissions": row["permissions"],
        }

    hooks.register_bundle({"notification_scope_resolver": current_membership}, source="current-member")
    monkeypatch.setattr(notification_router, "get_platform_hooks", lambda: hooks)
    return hooks


def _notification_member(workspace_id, tenant_id, *, roles=None, permissions=None, status="active"):
    return {
        "user_id": USER_ID, "workspace_id": workspace_id, "tenant_id": tenant_id,
        "roles": roles or [], "permissions": permissions or [], "status": status,
    }


def _seed_verified_grant_records(notification_http):
    notification_http.collection.insert_many([
        _role_record("grant-role-a", workspace_id=WORKSPACE_A, tenant_id=TENANT_A),
        _role_record("grant-role-b", workspace_id=WORKSPACE_B, tenant_id=TENANT_B),
        {**_role_record("grant-viewer-b", workspace_id=WORKSPACE_B, tenant_id=TENANT_B),
         "audience": {"roles": ["viewer"]}},
        {**_role_record("grant-permission-a", workspace_id=WORKSPACE_A, tenant_id=TENANT_A),
         "audience": {"permissions": ["support.read"]}},
        {**_role_record("grant-permission-b", workspace_id=WORKSPACE_B, tenant_id=TENANT_B),
         "audience": {"permissions": ["support.read"]}},
        _role_record("grant-ownerless-broad", ownerless=True),
        _role_record("grant-owner-array", workspace_id=[WORKSPACE_A, WORKSPACE_B], tenant_id=TENANT_A),
        {**_record("grant-direct-a", module_id="messages", event_type="domain.messages.message_sent",
                    workspace_id=WORKSPACE_A), "tenant_id": TENANT_A},
        {**_record("grant-empty-a", module_id="billing", event_type="domain.billing.updated",
                    workspace_id=WORKSPACE_A), "tenant_id": TENANT_A},
        {**_record("grant-tenant-a", module_id="billing", event_type="domain.billing.updated",
                    ownerless=True), "tenant_id": TENANT_A},
        {**_record("grant-direct-b", module_id="messages", event_type="domain.messages.message_sent",
                    workspace_id=WORKSPACE_B), "tenant_id": TENANT_B},
        {**_record("grant-foreign-app", module_id="billing", event_type="domain.billing.updated",
                    ownerless=True), "app_id": "foreign-app"},
    ])


def test_claimless_single_member_grants_all_five_routes_without_foreign_access(
    notification_http, monkeypatch,
):
    _seed_verified_grant_records(notification_http)
    _register_notification_memberships(notification_http, monkeypatch, [
        _notification_member(WORKSPACE_A, TENANT_A, roles=["owner"], permissions=["support.read"]),
    ])
    client = notification_http.client
    headers = notification_http.token(None, roles=["global-owner"])
    expected = {
        "support-a", "reply-a", "direct-message", "app-wide", "grant-role-a",
        "grant-permission-a", "grant-direct-a", "grant-empty-a", "grant-tenant-a",
    }
    response = client.get("/api/notifications", headers=headers)
    assert _ids(response) == expected
    for row in response.json()["notifications"]:
        assert not {"source_event", "tenant_id", "workspace_id", "audience"} & set(row)
    assert client.get("/api/notifications/count", headers=headers).json() == {
        "count": len(expected), "unread_count": len(expected),
    }
    for foreign in ("grant-role-b", "grant-permission-b", "grant-direct-b",
                    "grant-ownerless-broad", "grant-owner-array", "grant-foreign-app"):
        assert client.post(f"/api/notifications/{foreign}/read", headers=headers).json()["success"] is False
    assert client.post("/api/notifications/grant-role-a/read", headers=headers).json()["success"] is True
    assert client.post("/api/notifications/mark-all-read", headers=headers).json()["marked_count"] == len(expected) - 1
    assert client.delete("/api/notifications", headers=headers).json()["cleared_count"] == len(expected)
    assert notification_http.collection.count_documents({"notification_id": {"$in": [
        "grant-role-b", "grant-permission-b", "grant-direct-b", "grant-ownerless-broad",
        "grant-owner-array", "grant-foreign-app",
    ]}}) == 6


@pytest.mark.parametrize("memberships", [[], [
    _notification_member(WORKSPACE_A, TENANT_A, roles=["owner"]),
    _notification_member(WORKSPACE_B, TENANT_B, roles=["owner"]),
]])
def test_claimless_zero_or_multiple_memberships_hide_owned_alerts(
    notification_http, monkeypatch, memberships,
):
    _seed_verified_grant_records(notification_http)
    _register_notification_memberships(notification_http, monkeypatch, memberships)
    headers = notification_http.token(None, roles=["owner"])
    client = notification_http.client
    assert _ids(client.get("/api/notifications", headers=headers)) == {"direct-message", "app-wide"}
    assert client.get("/api/notifications/count", headers=headers).json()["count"] == 2
    assert client.post("/api/notifications/grant-direct-a/read", headers=headers).json()["success"] is False
    assert client.post("/api/notifications/mark-all-read", headers=headers).json()["marked_count"] == 2
    assert client.delete("/api/notifications", headers=headers).json()["cleared_count"] == 2
    assert notification_http.collection.count_documents({"notification_id": "grant-role-a"}) == 1


def test_token_bound_member_uses_its_exact_grants_and_revocation_is_immediate(
    notification_http, monkeypatch,
):
    _seed_verified_grant_records(notification_http)
    memberships = [
        _notification_member(WORKSPACE_A, TENANT_A, roles=["owner"]),
        _notification_member(WORKSPACE_B, TENANT_B, roles=["viewer"]),
    ]
    _register_notification_memberships(notification_http, monkeypatch, memberships)
    headers = notification_http.token(WORKSPACE_B, roles=["owner"])
    before = _ids(notification_http.client.get("/api/notifications", headers=headers))
    assert "grant-viewer-b" in before
    assert "grant-role-b" not in before  # token-global owner is not a B-membership owner
    assert "grant-role-a" not in before
    assert "grant-direct-b" in before
    memberships[1]["status"] = "suspended"
    assert _ids(notification_http.client.get("/api/notifications", headers=headers)) == {
        "direct-message", "app-wide",
    }
    assert notification_http.client.post(
        "/api/notifications/grant-direct-b/read", headers=headers,
    ).json()["success"] is False


@pytest.mark.parametrize("failure", ["missing", "error", "invalid", "duplicate"])
def test_notification_hook_failure_never_falls_back_to_module_scope(
    notification_http, failure,
):
    # The fixture's older module-scope hook would verify workspace A. Once a
    # notification-specific resolver is registered, its failure must not grant
    # any owner-bearing alert through that older path.
    hooks = notification_http.hooks

    def broken(**_kwargs):
        raise RuntimeError("membership backend unavailable")

    if failure == "error":
        hook = broken
    elif failure == "invalid":
        hook = lambda **_kwargs: {  # noqa: E731
            "app_id": APP_ID, "user_id": USER_ID, "tenant_id": TENANT_B,
            "workspace_id": WORKSPACE_B, "roles": ["owner"], "permissions": [],
        }
    else:
        hook = lambda **_kwargs: None  # noqa: E731
    hooks.register_bundle({"notification_scope_resolver": hook}, source="candidate")
    if failure == "duplicate":
        hooks.register_bundle({"notification_scope_resolver": hook}, source="duplicate")

    headers = notification_http.token(WORKSPACE_A, roles=["owner"])
    client = notification_http.client
    assert _ids(client.get("/api/notifications", headers=headers)) == {"direct-message", "app-wide"}
    assert client.get("/api/notifications/count", headers=headers).json()["count"] == 2
    assert client.post("/api/notifications/support-a/read", headers=headers).json()["success"] is False
    assert client.post("/api/notifications/mark-all-read", headers=headers).json()["marked_count"] == 2
    assert client.delete("/api/notifications", headers=headers).json()["cleared_count"] == 2
    assert notification_http.collection.count_documents({"notification_id": "support-a"}) == 1


def test_oidc_directory_tid_does_not_replace_internal_membership_tenant(notification_http, monkeypatch):
    _seed_verified_grant_records(notification_http)
    _register_notification_memberships(notification_http, monkeypatch, [
        _notification_member(WORKSPACE_A, TENANT_A, roles=["owner"]),
    ])
    headers = notification_http.token(WORKSPACE_A, tenant_id="entra-directory", roles=["owner"])
    visible = _ids(notification_http.client.get("/api/notifications", headers=headers))
    assert "grant-role-a" in visible
    assert "grant-role-b" not in visible
    assert notification_http.client.post(
        "/api/notifications/grant-role-a/read", headers=headers,
    ).json()["success"] is True


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


def test_broad_role_alerts_wait_for_exact_membership_grants(notification_http):
    _seed_role_notifications(notification_http)
    client = notification_http.client
    a = notification_http.token(WORKSPACE_A, roles=["owner"])
    b = notification_http.token(WORKSPACE_B, roles=["owner"])
    expected_a = {
        "support-a", "reply-a", "direct-message", "app-wide",
    }
    expected_b = {
        "support-b", "reply-b", "direct-message", "app-wide", "role-and-recipient-b",
    }
    assert _ids(client.get("/api/notifications", headers=a)) == expected_a
    assert _ids(client.get("/api/notifications", headers=b)) == expected_b
    assert _ids(client.get("/api/notifications", headers=notification_http.token(
        WORKSPACE_A, tenant_id="entra-directory", roles=["owner"],
    ))) == expected_a
    assert _ids(client.get("/api/notifications", headers=notification_http.token(WORKSPACE_A))) == {
        "support-a", "reply-a", "direct-message", "app-wide",
    }
    assert client.get("/api/notifications/count", headers=a).json() == {"count": 4, "unread_count": 4}
    assert client.get("/api/notifications/count", headers=b).json() == {"count": 5, "unread_count": 5}


@pytest.mark.parametrize("audience", [
    {"roles": ["owner"]},
    {"permissions": ["workspace_support.read"]},
])
def test_workspace_membership_cannot_promote_token_wide_grant(notification_http, monkeypatch, audience):
    hooks = PlatformHookRegistry()

    def verified_membership(*, principal, requested_scope, **_kwargs):
        memberships = {
            "owner-a": (WORKSPACE_A, TENANT_A),
            "viewer-b": (WORKSPACE_B, TENANT_B),
        }
        membership = memberships.get(principal.user_id)
        if not membership or requested_scope.get("workspace_id") != membership[0]:
            return {}
        return {"verified_workspace_id": membership[0], "verified_tenant_id": membership[1]}

    hooks.register_bundle({"module_scope_resolver": verified_membership}, source="exact-membership")
    monkeypatch.setattr(notification_router, "get_platform_hooks", lambda: hooks)
    broad = _role_record("workspace-b-broad", workspace_id=WORKSPACE_B, tenant_id=TENANT_B)
    broad["audience"] = audience
    direct = _record(
        "workspace-b-direct", module_id="messages", event_type="domain.messages.message_sent",
        workspace_id=WORKSPACE_B,
    )
    direct["audience"] = {"user_ids": ["viewer-b"]}
    notification_http.collection.insert_many([broad, direct])

    client = notification_http.client
    owner_a = notification_http.token(WORKSPACE_A, roles=["owner"], user_id="owner-a")
    viewer_b = notification_http.token(WORKSPACE_B, roles=["owner"], user_id="viewer-b")
    assert _ids(client.get("/api/notifications", headers=owner_a)) == {"app-wide"}
    assert _ids(client.get("/api/notifications", headers=viewer_b)) == {
        "app-wide", "workspace-b-direct",
    }
    assert client.get("/api/notifications/count", headers=viewer_b).json() == {
        "count": 2, "unread_count": 2,
    }
    assert client.post("/api/notifications/workspace-b-broad/read", headers=viewer_b).json()["success"] is False
    assert client.post("/api/notifications/mark-all-read", headers=viewer_b).json()["marked_count"] == 2
    assert client.delete("/api/notifications", headers=viewer_b).json()["cleared_count"] == 2
    assert notification_http.collection.find_one({"notification_id": "workspace-b-broad"})["status"] == "unread"


@pytest.mark.parametrize("event_shape", ["structured", "flat"])
@pytest.mark.parametrize("audience", [{"roles": ["owner"]}, {"permissions": ["workspace_support.read"]}])
def test_generated_workspace_broad_alert_keeps_owner_but_waits_for_grant(
    notification_http, event_shape, audience,
):
    notification_id = f"generated-{event_shape}-{'role' if 'roles' in audience else 'permission'}"

    async def store(record):
        record["notification_id"] = notification_id
        notification_http.collection.insert_one(record)

    event_type = "domain.workspace_support.request_created"
    rule = {
        "id": "workspace_support.created", "module_id": "workspace_support",
        "audience": audience,
        "template": {"title": "New support request", "body": "Review the request"},
    }
    envelope = {
        "id": f"event-{notification_id}",
        "type": event_type,
        "app_id": APP_ID,
        "workspace_id": WORKSPACE_A,
    }
    if event_shape == "structured":
        envelope = {
            "id": f"event-{notification_id}",
            "type": event_type,
            "tenant": {"app_id": APP_ID, "workspace_id": WORKSPACE_A},
            "payload": {"request_id": notification_id},
        }

    before = notification_http.client.get(
        "/api/notifications/count", headers=notification_http.token(WORKSPACE_A, roles=["owner"]),
    ).json()["count"]
    asyncio.run(ModuleEventRouter([], notification_store=store)._create_notification(
        rule, event_type, envelope,
    ))
    stored = notification_http.collection.find_one({"notification_id": notification_id})
    assert stored is not None
    assert "tenant_id" not in stored
    assert stored["workspace_id"] == WORKSPACE_A

    client = notification_http.client
    member_a = notification_http.token(WORKSPACE_A, roles=["owner"])
    member_b = notification_http.token(WORKSPACE_B, roles=["owner"])
    unbound = notification_http.token(None, roles=["owner"])
    assert notification_id not in _ids(client.get("/api/notifications", headers=member_a))
    assert client.get("/api/notifications/count", headers=member_a).json()["count"] == before
    assert notification_id not in _ids(client.get("/api/notifications", headers=member_b))
    assert notification_id not in _ids(client.get("/api/notifications", headers=unbound))
    assert client.post(f"/api/notifications/{notification_id}/read", headers=member_b).json()["success"] is False
    assert client.post(f"/api/notifications/{notification_id}/read", headers=member_a).json()["success"] is False
    assert client.get("/api/notifications/count", headers=member_a).json()["count"] == before


@pytest.mark.parametrize("event_shape", ["structured", "flat"])
def test_generated_tenant_broad_alert_keeps_owner_but_waits_for_grant(
    notification_http, event_shape,
):
    notification_id = f"tenant-only-{event_shape}"

    async def store(record):
        record["notification_id"] = notification_id
        notification_http.collection.insert_one(record)

    event_type = "hosted.hosting.app.deployed"
    envelope = {
        "id": f"event-{notification_id}", "type": event_type,
        "app_id": APP_ID, "tenant_id": TENANT_A,
    }
    if event_shape == "structured":
        envelope = {
            "id": f"event-{notification_id}", "type": event_type,
            "tenant": {"app_id": APP_ID, "tenant_id": TENANT_A},
            "payload": {"hosting_url": "https://example.invalid"},
        }
    asyncio.run(ModuleEventRouter([], notification_store=store)._create_notification(
        {
            "id": "hosting_deployed.user", "module_id": "hosting",
            "audience": {"roles": ["owner"]},
            "template": {"title": "App deployed", "body": "Deployment complete"},
        },
        event_type,
        envelope,
    ))
    stored = notification_http.collection.find_one({"notification_id": notification_id})
    assert stored is not None
    assert stored["tenant_id"] == TENANT_A
    assert "workspace_id" not in stored

    client = notification_http.client
    member_a = notification_http.token(WORKSPACE_A, roles=["owner"])
    member_b = notification_http.token(WORKSPACE_B, roles=["owner"])
    assert notification_id not in _ids(client.get("/api/notifications", headers=member_a))
    assert notification_id not in _ids(client.get("/api/notifications", headers=member_b))
    assert notification_id not in _ids(client.get(
        "/api/notifications", headers=notification_http.token(None, roles=["owner"]),
    ))


@pytest.mark.parametrize("event_shape", ["structured", "flat"])
@pytest.mark.parametrize("invalid_owner", ["tenant_id", "workspace_id"])
def test_malformed_generated_owner_never_broadens_role_alert(
    notification_http, event_shape, invalid_owner,
):
    event_type = "hosted.hosting.app.deployed"
    owner = {"app_id": APP_ID, "tenant_id": TENANT_A, "workspace_id": WORKSPACE_A}
    owner[invalid_owner] = []
    envelope = {"id": f"event-invalid-{event_shape}-{invalid_owner}", "type": event_type}
    if event_shape == "structured":
        envelope["tenant"] = owner
        envelope["payload"] = {"hosting_url": "https://example.invalid"}
    else:
        envelope.update(owner)

    async def store(record):
        notification_http.collection.insert_one(record)

    asyncio.run(ModuleEventRouter([], notification_store=store)._create_notification(
        {
            "id": "hosting_deployed.user", "module_id": "hosting",
            "audience": {"roles": ["owner"]},
            "template": {"title": "App deployed", "body": "Deployment complete"},
        },
        event_type,
        envelope,
    ))
    assert notification_http.collection.count_documents({
        "source_event_id": envelope["id"],
    }) == 0


@pytest.mark.parametrize("nested_owner", ["", None])
@pytest.mark.parametrize("owner_key", ["tenant_id", "workspace_id"])
def test_generated_owner_fallback_matches_provenance_and_stays_hidden(
    notification_http, owner_key, nested_owner,
):
    event_type = "hosted.hosting.app.deployed"
    notification_id = f"fallback-{owner_key}-{nested_owner!r}"
    tenant = {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A}
    tenant[owner_key] = nested_owner
    top_owner = TENANT_B if owner_key == "tenant_id" else WORKSPACE_A
    envelope = {
        "id": f"event-{notification_id}", "type": event_type,
        "tenant": tenant, "app_id": APP_ID, owner_key: top_owner,
        "payload": {"hosting_url": "https://example.invalid"},
    }
    emitted = []

    async def store(record):
        record["notification_id"] = notification_id
        notification_http.collection.insert_one(record)

    async def emit(_event_type, event):
        emitted.append(event)

    asyncio.run(ModuleEventRouter(
        [], notification_store=store, event_emitter=emit,
    )._create_notification({
        "id": "hosting_deployed.user", "module_id": "hosting",
        "audience": {"roles": ["owner"]},
        "template": {"title": "App deployed", "body": "Deployment complete"},
    }, event_type, envelope))
    record = notification_http.collection.find_one({"notification_id": notification_id})
    assert record is not None
    expected = {
        "app_id": APP_ID,
        "tenant_id": TENANT_B if owner_key == "tenant_id" else TENANT_A,
        "workspace_id": WORKSPACE_A,
    }
    assert {key: record[key] for key in expected} == expected
    assert [event["tenant"] for event in emitted] == [expected, expected]

    member_a = notification_http.token(WORKSPACE_A, roles=["owner"])
    member_b = notification_http.token(WORKSPACE_B, roles=["owner"])
    assert notification_id not in _ids(notification_http.client.get(
        "/api/notifications", headers=member_a,
    ))
    assert notification_id not in _ids(notification_http.client.get(
        "/api/notifications", headers=member_b,
    ))


@pytest.mark.parametrize("owner_key", ["tenant_id", "workspace_id"])
def test_conflicting_generated_owner_fields_create_no_role_alert(notification_http, owner_key):
    event_type = "hosted.hosting.app.deployed"
    tenant = {"app_id": APP_ID, "tenant_id": TENANT_A, "workspace_id": WORKSPACE_A}
    envelope = {
        "id": f"event-conflict-{owner_key}", "type": event_type,
        "tenant": tenant,
        owner_key: TENANT_B if owner_key == "tenant_id" else WORKSPACE_B,
        "payload": {"hosting_url": "https://example.invalid"},
    }

    async def store(record):
        notification_http.collection.insert_one(record)

    asyncio.run(ModuleEventRouter([], notification_store=store)._create_notification({
        "id": "hosting_deployed.user", "module_id": "hosting",
        "audience": {"roles": ["owner"]},
        "template": {"title": "App deployed", "body": "Deployment complete"},
    }, event_type, envelope))
    assert notification_http.collection.count_documents({"source_event_id": envelope["id"]}) == 0


def test_present_null_owners_do_not_authorize_a_broad_alert(notification_http):
    record = _role_record("malformed-null-owners", workspace_id=None)
    record["tenant_id"] = None
    notification_http.collection.insert_one(record)
    for workspace_id in (WORKSPACE_A, WORKSPACE_B, None):
        headers = notification_http.token(workspace_id, roles=["owner"])
        assert "malformed-null-owners" not in _ids(notification_http.client.get(
            "/api/notifications", headers=headers,
        ))
        assert notification_http.client.post(
            "/api/notifications/malformed-null-owners/read", headers=headers,
        ).json()["success"] is False


def test_broad_role_alert_mutations_require_membership_grants(notification_http):
    _seed_role_notifications(notification_http)
    client = notification_http.client
    a = notification_http.token(WORKSPACE_A, roles=["owner"])
    foreign = {
        "role-b", "role-tenant-b", "role-workspace-b", "role-and-recipient-b", "role-announcement-b",
        "role-ownerless", "role-workspace-array", "role-tenant-array", "role-mismatched-owner",
    }
    for notification_id in foreign:
        assert client.post(f"/api/notifications/{notification_id}/read", headers=a).json()["success"] is False
    assert client.post("/api/notifications/role-a/read", headers=a).json()["success"] is False
    assert client.post("/api/notifications/mark-all-read", headers=a).json()["marked_count"] == 4
    assert notification_http.collection.count_documents({"notification_id": {"$in": list(foreign)},
                                                         "status": "unread"}) == len(foreign)
    assert client.delete("/api/notifications", headers=a).json()["cleared_count"] == 4
    assert notification_http.collection.count_documents({"module_id": "hosting", "status": "unread"}) == 11


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


def test_workspace_only_membership_does_not_authorize_broad_role(notification_http, monkeypatch):
    _seed_role_notifications(notification_http)
    hooks = PlatformHookRegistry()
    hooks.register_bundle({
        "module_scope_resolver": lambda **_kwargs: {"verified_workspace_id": WORKSPACE_A},
    }, source="workspace-only-membership")
    monkeypatch.setattr(notification_router, "get_platform_hooks", lambda: hooks)
    a = notification_http.token(WORKSPACE_A, roles=["owner"])
    assert _ids(notification_http.client.get("/api/notifications", headers=a)) == {
        "support-a", "reply-a", "direct-message", "app-wide",
    }
    assert notification_http.client.post("/api/notifications/role-a/read", headers=a).json()["success"] is False
    assert notification_http.client.post("/api/notifications/role-workspace-a/read", headers=a).json()["success"] is False


def test_direct_recipient_derivation_never_turns_missing_target_app_wide(notification_http):
    async def store(record):
        notification_http.collection.insert_one(record)

    router = ModuleEventRouter([], notification_store=store)
    rule = {
        "id": "message_sent", "module_id": "messages",
        "audience": {"user_id_field": "recipient_ids"},
        "template": {"title": "New message", "body": "Message available"},
    }

    async def produce():
        for probe_id, recipients in (
            ("missing", None), ("empty", []), ("blank", [" "]),
            ("valid", [USER_ID]),
        ):
            payload = {"probe_id": probe_id}
            if recipients is not None:
                payload["recipient_ids"] = recipients
            await router._create_notification(rule, "domain.messages.message_sent", {
                "id": f"event-{probe_id}",
                "tenant": {"app_id": APP_ID},
                "payload": payload,
            })

    asyncio.run(produce())
    generated = list(notification_http.collection.find({"rule_id": "message_sent"}))
    assert len(generated) == 1
    assert generated[0]["audience"]["user_ids"] == [USER_ID]
    assert "event-valid" == generated[0]["source_event_id"]
    assert generated[0]["notification_id"] in _ids(notification_http.client.get(
        "/api/notifications", headers=notification_http.token(None),
    ))
    assert generated[0]["notification_id"] not in _ids(notification_http.client.get(
        "/api/notifications", headers=notification_http.token(None, user_id="another-user"),
    ))


def test_permission_audience_requires_exact_grant_for_reads_and_mutations(notification_http):
    def permission_record(notification_id, *, workspace_id=None, tenant_id=None, ownerless=False):
        record = _record(
            notification_id, module_id="infra_assurance",
            event_type="hosted.ops.infra_assurance.issue.detected",
            workspace_id=workspace_id, ownerless=ownerless,
        )
        record["audience"] = {"permissions": ["workspace_support.read"]}
        if tenant_id is not None:
            record["tenant_id"] = tenant_id
        return record

    notification_http.collection.insert_many([
        permission_record("permission-a", workspace_id=WORKSPACE_A, tenant_id=TENANT_A),
        permission_record("permission-b", workspace_id=WORKSPACE_B, tenant_id=TENANT_B),
        permission_record("permission-tenant-b", ownerless=True, tenant_id=TENANT_B),
        permission_record("permission-ownerless", ownerless=True),
        permission_record("permission-mismatch", workspace_id=WORKSPACE_A, tenant_id=TENANT_B),
        {
            **permission_record("permission-and-recipient-b", workspace_id=WORKSPACE_B, tenant_id=TENANT_B),
            "audience": {"permissions": ["workspace_support.read"], "user_ids": [USER_ID]},
        },
    ])
    client = notification_http.client
    a = notification_http.token(WORKSPACE_A)
    b = notification_http.token(WORKSPACE_B)
    unbound = notification_http.token(None)
    assert _ids(client.get("/api/notifications", headers=a)) == {
        "support-a", "reply-a", "direct-message", "app-wide",
    }
    assert _ids(client.get("/api/notifications", headers=b)) == {
        "support-b", "reply-b", "direct-message", "app-wide", "permission-and-recipient-b",
    }
    assert _ids(client.get("/api/notifications", headers=unbound)) == {"direct-message", "app-wide"}
    assert client.get("/api/notifications/count", headers=a).json() == {"count": 4, "unread_count": 4}
    assert client.get("/api/notifications/count", headers=b).json() == {"count": 5, "unread_count": 5}
    for notification_id in (
        "permission-b", "permission-tenant-b", "permission-ownerless", "permission-mismatch",
        "permission-and-recipient-b",
    ):
        assert client.post(f"/api/notifications/{notification_id}/read", headers=a).json()["success"] is False
        assert client.post(f"/api/notifications/{notification_id}/read", headers=unbound).json()["success"] is False
    assert client.post("/api/notifications/permission-a/read", headers=a).json()["success"] is False
    assert client.post("/api/notifications/mark-all-read", headers=a).json()["marked_count"] == 4
    assert client.delete("/api/notifications", headers=a).json()["cleared_count"] == 4
    assert notification_http.collection.count_documents({
        "notification_id": {"$regex": "^permission-"}, "status": "unread",
    }) == 6


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


def test_revoked_membership_hides_owned_direct_notification_on_all_routes(notification_http, monkeypatch):
    owned = _record(
        "direct-revoked", module_id="messages", event_type="domain.messages.message_sent",
        workspace_id=WORKSPACE_B,
    )
    owned["tenant_id"] = TENANT_B
    notification_http.collection.insert_one(owned)
    client = notification_http.client
    revoked_b = notification_http.token(WORKSPACE_B)
    assert "direct-revoked" in _ids(client.get("/api/notifications", headers=revoked_b))

    hooks = PlatformHookRegistry()

    def active_a_only(*, requested_scope, **_kwargs):
        if requested_scope.get("workspace_id") == WORKSPACE_A:
            return {"verified_workspace_id": WORKSPACE_A, "verified_tenant_id": TENANT_A}
        return {}

    hooks.register_bundle({"module_scope_resolver": active_a_only}, source="current-membership")
    monkeypatch.setattr(notification_router, "get_platform_hooks", lambda: hooks)

    assert _ids(client.get("/api/notifications", headers=revoked_b)) == {"direct-message", "app-wide"}
    assert client.get("/api/notifications/count", headers=revoked_b).json() == {
        "count": 2, "unread_count": 2,
    }
    assert client.post("/api/notifications/direct-revoked/read", headers=revoked_b).json()["success"] is False
    assert client.post("/api/notifications/mark-all-read", headers=revoked_b).json()["marked_count"] == 2
    assert client.delete("/api/notifications", headers=revoked_b).json()["cleared_count"] == 2
    assert notification_http.collection.find_one({"notification_id": "direct-revoked"})["status"] == "unread"


def test_workspace_owned_empty_audience_requires_matching_member_on_all_routes(notification_http):
    owned = _record(
        "owned-empty-a", module_id="billing", event_type="domain.billing.updated",
        workspace_id=WORKSPACE_A,
    )
    owned["tenant_id"] = TENANT_A
    notification_http.collection.insert_one(owned)

    client = notification_http.client
    assert "owned-empty-a" in _ids(client.get(
        "/api/notifications", headers=notification_http.token(WORKSPACE_A),
    ))
    member_b = notification_http.token(WORKSPACE_B)
    assert _ids(client.get("/api/notifications", headers=member_b)) == {
        "support-b", "reply-b", "direct-message", "app-wide",
    }
    assert client.get("/api/notifications/count", headers=member_b).json() == {
        "count": 4, "unread_count": 4,
    }
    assert client.post("/api/notifications/owned-empty-a/read", headers=member_b).json()["success"] is False
    assert client.post("/api/notifications/mark-all-read", headers=member_b).json()["marked_count"] == 4
    assert client.delete("/api/notifications", headers=member_b).json()["cleared_count"] == 4
    assert notification_http.collection.find_one({"notification_id": "owned-empty-a"})["status"] == "unread"


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
