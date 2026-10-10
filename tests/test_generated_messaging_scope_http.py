"""Signed HTTP dispatch of a materialized messaging pack binds conversation scope."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_templates_for_pack,
)
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
from mozaiksai.core.auth.config import clear_auth_config_cache
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.hosts.routers import modules as module_router

APP_ID = "generated-messaging-app"
OWN = "workspace-own"
FOREIGN = "workspace-foreign"
PACK = Path(__file__).resolve().parents[1] / "factory_app/build_context/messaging"


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict) and "$ne" in expected:
            if actual == expected["$ne"]:
                return False
        elif isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, fields):
        for field, direction in reversed(fields):
            self.rows.sort(key=lambda row: str(row.get(field) or ""), reverse=direction < 0)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length=None):
        return [dict(row) for row in self.rows[:length]] if length is not None else [dict(row) for row in self.rows]


class _Collection:
    def __init__(self):
        self.rows = []

    async def insert_one(self, document, **_options):
        self.rows.append(dict(document))
        return SimpleNamespace(inserted_id=len(self.rows))

    async def find_one(self, query, projection=None, **_options):
        return next((dict(row) for row in self.rows if _matches(row, query)), None)

    def find(self, query, projection=None, **_options):
        return _Cursor([row for row in self.rows if _matches(row, query)])

    async def update_one(self, query, update, *, upsert=False, **_options):
        for row in self.rows:
            if _matches(row, query):
                row.update(update["$set"])
                return SimpleNamespace(matched_count=1)
        if upsert:
            self.rows.append({**query, **update["$set"]})
        return SimpleNamespace(matched_count=0)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_AUDIENCE", "generated-messaging-test")
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "generated-messaging-test", "alg": "RS256", "use": "sig"})
    adapter = GenericJWTAdapter(config=JWTAdapterConfig(
        jwks_url="https://auth.test/jwks", issuer="https://auth.test", audience="generated-messaging-test",
    ))
    monkeypatch.setattr(adapter, "_get_jwks_client_async", AsyncMock(return_value=SimpleNamespace(
        get_signing_key=AsyncMock(return_value=jwk),
    )))
    monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", lambda: adapter)
    monkeypatch.setattr(module_router, "record_action_invocation", lambda **_kwargs: None)
    hooks = PlatformHookRegistry()
    monkeypatch.setattr(module_router, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr("mozaiksai.core.runtime.composition.module_executor.get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(ModuleExecutor, "_emit_dispatch_audit", AsyncMock())

    app_root = tmp_path / "app"
    emitted = resolve_templates_for_pack(PACK, "messaging")
    for file in emitted:
        if file["filename"].startswith("modules/messages/"):
            path = app_root / file["filename"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(file["content"], encoding="utf-8")
    loaded = ModuleLoader(str(app_root)).load("messages")
    database = defaultdict(lambda: defaultdict(_Collection))
    executor = ModuleExecutor(persistence_client=database, persistence_database="generated_messaging_test")
    executor.register_loaded_module(loaded)
    app = FastAPI()
    app.state.loaded_app_id = APP_ID
    app.state.module_action_surfaces = {"messages": loaded.action_api_surface_map}
    app.state.executor_registry = SimpleNamespace(module_executor=executor)
    app.include_router(module_router.router)

    def token(*, workspace=None, tenant=None):
        claims = {"sub": "alice", "iss": "https://auth.test", "aud": "generated-messaging-test",
                  "app_id": APP_ID, "scp": "messages.read messages.write", "exp": int(time.time()) + 300}
        if workspace:
            claims["workspace_id"] = workspace
        if tenant:
            claims["tid"] = tenant
        return {"Authorization": "Bearer " + jwt.encode(
            claims, key, algorithm="RS256", headers={"kid": "generated-messaging-test", "typ": "at+jwt"},
        )}

    def threads():
        return [row for name, collection in database["generated_messaging_test"].items()
                if name.endswith("threads") and not name.endswith("thread_reads") for row in collection.rows]

    def rows(suffix):
        return [row for name, collection in database["generated_messaging_test"].items()
                if name.endswith(suffix) for row in collection.rows]

    yield SimpleNamespace(client=TestClient(app, raise_server_exceptions=False), token=token,
                          hooks=hooks, threads=threads, rows=rows)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()


def _create(runtime, token, *, params=None, context=None):
    body = {"params": {"participant_ids": ["alice"], **(params or {})}}
    if context is not None:
        body["context"] = context
    return runtime.client.post("/api/modules/messages/create_thread", json=body, headers=token)


def _action(runtime, token, action, *, params=None, context=None):
    body = {"params": params or {}}
    if context is not None:
        body["context"] = context
    return runtime.client.post(f"/api/modules/messages/{action}", json=body, headers=token)


@pytest.mark.parametrize(("params", "context"), [
    ({"scope_type": "workspace"}, {"workspace_id": FOREIGN}),
    ({"scope_type": "workspace", "scope_id": FOREIGN}, None),
])
def test_unbound_signed_token_cannot_create_foreign_workspace_thread(runtime, params, context):
    response = _create(runtime, runtime.token(), params=params, context=context)
    assert response.status_code == 403, response.text
    assert runtime.threads() == []


def test_bound_signed_token_rejects_foreign_workspace_param(runtime):
    response = _create(runtime, runtime.token(workspace=OWN),
                       params={"scope_type": "workspace", "scope_id": FOREIGN})
    assert response.status_code == 403, response.text
    assert runtime.threads() == []


def test_verified_membership_allows_own_workspace_and_rejects_conflicts(runtime):
    runtime.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"verified_workspace_id": OWN}}, source="test",
    )
    token = runtime.token()
    own = _create(runtime, token, params={"scope_type": "workspace"})
    assert own.status_code == 200, own.text
    assert own.json()["thread"]["scope_id"] == OWN

    for params, context in [
        ({"scope_type": "workspace", "scope_id": FOREIGN}, None),
        ({"scope_type": "workspace"}, {"workspace_id": FOREIGN}),
        ({"scope_type": "workspace"}, {"tenant_id": "unverified-tenant"}),
    ]:
        refused = _create(runtime, token, params=params, context=context)
        assert refused.status_code == 403, refused.text
    assert len(runtime.threads()) == 1


def test_unbound_token_cannot_read_or_mutate_participant_thread_in_foreign_workspace(runtime):
    foreign = _create(runtime, runtime.token(workspace=FOREIGN), params={"scope_type": "workspace"})
    assert foreign.status_code == 200, foreign.text
    thread_id = foreign.json()["thread"]["thread_id"]
    token = runtime.token()
    context = {"workspace_id": FOREIGN}

    listed = _action(runtime, token, "list_threads", params={"scope_type": "workspace"}, context=context)
    assert listed.status_code == 403, listed.text
    read = _action(runtime, token, "get_thread", params={"thread_id": thread_id}, context=context)
    assert read.status_code == 200, read.text
    assert read.json()["thread"] is None
    sent = _action(runtime, token, "send_message", params={"thread_id": thread_id, "body": "unauthorized"},
                   context=context)
    assert sent.status_code == 200, sent.text
    assert sent.json()["success"] is False
    marked = _action(runtime, token, "mark_thread_read", params={"thread_id": thread_id}, context=context)
    assert marked.status_code == 200, marked.text
    assert marked.json()["success"] is False
    assert runtime.rows("messages") == []
    assert runtime.rows("thread_reads") == []


def test_local_development_keeps_explicit_workspace_selection(runtime, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_ANON_ACCESS", "local")
    monkeypatch.delenv("AUTH_PROVIDER")
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()
    with TestClient(runtime.client.app, client=("127.0.0.1", 50000), base_url="http://localhost:8000") as local:
        created = local.post("/api/modules/messages/create_thread", json={
            "context": {"workspace_id": OWN},
            "params": {"scope_type": "workspace"},
        })
    assert created.status_code == 200, created.text
    assert created.json()["thread"]["scope_id"] == OWN


def test_app_scope_uses_loaded_app_and_rejects_foreign_scope(runtime):
    token = runtime.token()
    own = _create(runtime, token)
    assert own.status_code == 200, own.text
    assert own.json()["thread"]["scope_id"] == APP_ID
    foreign = _create(runtime, token, params={"scope_type": "app", "scope_id": "foreign-app"})
    assert foreign.status_code == 403, foreign.text
    assert len(runtime.threads()) == 1
