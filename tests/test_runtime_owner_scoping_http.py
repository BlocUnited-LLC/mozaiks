"""HTTP ownership proofs with signed tokens and the real persistence adapter.

Only the Mongo driver and JWKS transport are replaced. Authentication, router,
executor, compiler-built reads, and persistence ownership enforcement are real.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.workflow.generator_support.module_read_actions import _read_functions
from mozaiksai.hosts.routers import modules as module_router


def _matches(document, query):
    return all(
        all(_matches(document, item) for item in value)
        if key == "$and" else
        not any(_matches(document, item) for item in value)
        if key == "$nor" else
        isinstance(document.get(key), list)
        if value == {"$type": "array"} else document.get(key) == value
        for key, value in query.items()
    )


class _Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def sort(self, fields):
        for name, direction in reversed(fields):
            self.rows.sort(key=lambda row: row[name], reverse=direction < 0)
        return self

    def limit(self, limit):
        self.rows = self.rows[:limit]
        return self

    async def to_list(self, length=None):
        return deepcopy(self.rows if length is None else self.rows[:length])


class _Collection:
    def __init__(self):
        self.rows = []

    async def insert_one(self, document):
        self.rows.append({"_id": len(self.rows) + 1, **deepcopy(document)})

    def find(self, query, projection=None, **kwargs):
        return _Cursor([row for row in self.rows if _matches(row, query)])

    async def find_one(self, query, projection=None, **kwargs):
        rows = await self.find(query).to_list(length=1)
        return rows[0] if rows else None

    async def count_documents(self, query, **kwargs):
        return len(await self.find(query).to_list())

    async def update_one(self, query, update, *, upsert=False, **kwargs):
        assert not upsert
        for row in self.rows:
            if _matches(row, query):
                row.update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    async def delete_one(self, query, **kwargs):
        for row in self.rows:
            if _matches(row, query):
                self.rows.remove(row)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def aggregate(self, pipeline, **kwargs):
        rows = deepcopy(self.rows)
        for stage in pipeline:
            if "$match" in stage:
                rows = [row for row in rows if _matches(row, stage["$match"])]
            elif "$sort" in stage:
                rows = _Cursor(rows).sort(list(stage["$sort"].items())).rows
            elif "$skip" in stage:
                rows = rows[stage["$skip"]:]
            elif "$limit" in stage:
                rows = rows[:stage["$limit"]]
            elif "$group" in stage:
                assert stage["$group"] == {"_id": None, "count": {"$sum": 1}}
                rows = [{"_id": None, "count": len(rows)}] if rows else []
            else:
                raise AssertionError(f"Unsafe aggregation reached Mongo: {stage}")
        return _Cursor(rows)


def _contract(tenancy):
    owner = "created_by" if tenancy == "per_user" else "workspace_owner"
    collection = {
        "name": "tasks", "entity": "Task", "scope": "app", "tenancy": tenancy,
        "owner_field": None if tenancy == "app_wide" else owner,
        "ownership": {"surface_id": "tasks", "surface_kind": "module"},
        "fields": [{"name": name, "type": "string", "required": True}
                   for name in ("task_id", "title", owner)],
        "search_by": "task_id",
    }
    return {"version": "1", "surfaces": [{
        "surface_id": "tasks", "surface_kind": "module", "collections": [collection],
    }]}


class _ModelWrittenHandler:
    """The vulnerable repository patterns from the observed ServiceAgent run."""

    def __init__(self, contract):
        collection = contract["surfaces"][0]["collections"][0]
        namespace = {}
        for operation in ("get", "list"):
            exec(_read_functions("tasks", collection, operation)[2], namespace)
        self._code_reads = namespace

    async def create_task(self, ctx, *, task_id, title, **fields):
        await ctx.persistence.collection("tasks", "tasks").insert_one({
            "task_id": task_id, "title": title, "owner_id": ctx.user_id, **fields,
        })
        return {"task_id": task_id}

    async def update_task(self, ctx, *, task_id, title):
        result = await ctx.persistence.collection("tasks", "tasks").update_one(
            {"task_id": task_id}, {"$set": {"title": title}},
        )
        return {"matched": result.matched_count}

    async def delete_task(self, ctx, *, task_id):
        result = await ctx.persistence.collection("tasks", "tasks").delete_one({"task_id": task_id})
        return {"deleted": result.deleted_count}

    async def custom_read(self, ctx, *, task_id):
        return {"item": await ctx.persistence.collection("tasks", "tasks").find_one({"task_id": task_id})}

    async def custom_list(self, ctx):
        return {"items": await ctx.persistence.collection("tasks", "tasks").find_many({})}

    async def task_summary(self, ctx):
        return {"rows": await ctx.persistence.collection("tasks", "tasks").aggregate([
            {"$group": {"_id": None, "count": {"$sum": 1}}},
        ])}

    async def foreign_aggregate(self, ctx):
        return await ctx.persistence.collection("tasks", "tasks").aggregate([
            {"$unionWith": "foreign_tasks"},
        ])

    async def list_tasks(self, ctx):
        return await self._code_reads["list_tasks"](ctx)

    async def get_tasks(self, ctx, *, id):
        return await self._code_reads["get_tasks"](ctx, id=id)

    async def relationships(self, ctx):
        rows = await ctx.persistence.collection("tasks", "tasks").find_many({})
        return {"items": [{"resource_type": "task", "resource_id": row["task_id"],
                           "relationship_type": "owner"} for row in rows]}


@pytest.fixture
def http_runtime(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "ownership-test", "alg": "RS256", "use": "sig"})
    adapter = GenericJWTAdapter(config=JWTAdapterConfig(
        jwks_url="https://auth.test/jwks", issuer="https://auth.test", audience="ownership-test",
    ))
    monkeypatch.setattr(adapter, "_get_jwks_client_async", AsyncMock(return_value=SimpleNamespace(
        get_signing_key=AsyncMock(return_value=jwk),
    )))
    monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", lambda: adapter)
    monkeypatch.setattr(module_router, "record_action_invocation", lambda **kwargs: None)
    hooks = PlatformHookRegistry()
    monkeypatch.setattr(module_router, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr("mozaiksai.core.runtime.composition.module_executor.get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(ModuleExecutor, "_emit_dispatch_audit", AsyncMock())
    database = defaultdict(_Collection)
    monkeypatch.setattr("mozaiksai.core.runtime.persistence.mongo.get_mongo_client", lambda: defaultdict(lambda: database))

    def token(user, workspace=None):
        claims = {"sub": user, "iss": "https://auth.test", "aud": "ownership-test",
                  "app_id": "ownership-test-app", "exp": int(time.time()) + 300}
        if workspace is not None:
            claims["workspace_id"] = workspace
        return {"Authorization": "Bearer " + jwt.encode(
            claims, key, algorithm="RS256", headers={"kid": "ownership-test"},
        )}

    def client(tenancy="per_user", *, ownerless=False):
        contract = _contract(tenancy)
        handler = _ModelWrittenHandler(contract)
        if ownerless:
            collection = contract["surfaces"][0]["collections"][0]
            for field in ("tenancy", "owner_field", "entity"):
                collection.pop(field)
        executor = ModuleExecutor(data_contract=contract)
        actions = {name: name for name in (
            "create_task", "update_task", "delete_task", "custom_read", "custom_list",
            "task_summary", "foreign_aggregate", "list_tasks", "get_tasks",
            "relationships",
        )}
        executor.register("tasks", handler, action_method_map=actions)
        app = FastAPI()
        app.state.module_action_surfaces = {"tasks": dict.fromkeys(actions)}
        app.state.executor_registry = SimpleNamespace(module_executor=executor)
        app.include_router(module_router.router)
        return TestClient(app)

    return SimpleNamespace(client=client, token=token, hooks=hooks, database=database)


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
def test_two_authenticated_owners_are_isolated_for_model_and_code_actions(http_runtime, tenancy):
    client = http_runtime.client(tenancy)
    user_a = http_runtime.token("user-a", "workspace-a")
    user_b = http_runtime.token("user-b", "workspace-b")
    url = "/api/modules/tasks/"
    response = client.post(url + "create_task", headers=user_a, json={"task_id": "a", "title": "A task"})
    assert response.status_code == 200, response.text
    for action in ("list_tasks", "custom_list"):
        own = client.get(url + action, headers=user_a)
        assert own.status_code == 200, own.text
        assert [row["task_id"] for row in own.json()["items"]] == ["a"]
        other = client.get(url + action, headers=user_b)
        assert other.status_code == 200, other.text
        assert other.json()["items"] == []
    own = client.get(url + "get_tasks?id=a", headers=user_a)
    assert own.status_code == 200, own.text
    assert own.json()["item"]["task_id"] == "a"
    other = client.get(url + "get_tasks?id=a", headers=user_b)
    assert other.status_code >= 400
    assert "A task" not in other.text
    assert client.get(url + "custom_read?task_id=a", headers=user_b).json() == {"item": None}
    assert client.get(url + "task_summary", headers=user_b).json() == {"rows": []}
    assert client.get(url + "task_summary", headers=user_a).json() == {"rows": [{"_id": None, "count": 1}]}
    assert client.post(url + "update_task", headers=user_b, json={"task_id": "a", "title": "stolen"}).json() == {"matched": 0}
    assert client.post(url + "delete_task", headers=user_b, json={"task_id": "a"}).json() == {"deleted": 0}
    assert client.get(url + "custom_read?task_id=a", headers=user_a).json()["item"]["title"] == "A task"
    assert client.post(url + "update_task", headers=user_a, json={"task_id": "a", "title": "edited"}).json() == {"matched": 1}
    assert client.post(url + "delete_task", headers=user_a, json={"task_id": "a"}).json() == {"deleted": 1}


@pytest.mark.parametrize("tenancy,owner_field", [("per_user", "created_by"), ("per_workspace", "workspace_owner")])
def test_client_cannot_choose_owner_or_escape_through_aggregation(http_runtime, tenancy, owner_field):
    client = http_runtime.client(tenancy)
    headers = http_runtime.token("user-b", "workspace-b")
    response = client.post("/api/modules/tasks/create_task", headers=headers, json={
        "task_id": "forged", "title": "forged", owner_field: "foreign-owner",
    })
    assert response.status_code == 403, response.text
    assert client.get("/api/modules/tasks/list_tasks", headers=headers).json() == {"items": [], "total": 0}
    response = client.get("/api/modules/tasks/foreign_aggregate", headers=headers)
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("shape", ["query", "context", "flat", "hook"])
def test_unbound_token_cannot_gain_workspace_ownership_from_request(http_runtime, shape):
    client = http_runtime.client("per_workspace")
    headers = http_runtime.token("user-b")
    url = "/api/modules/tasks/list_tasks"
    if shape == "query":
        response = client.get(url + "?workspace_id=workspace-a", headers=headers)
    elif shape == "hook":
        http_runtime.hooks.register_bundle({"module_scope_resolver": lambda **kwargs: {
            **kwargs["requested_scope"], "workspace_id": "workspace-a",
        }}, source="test-host")
        response = client.get(url, headers=headers)
    else:
        body = {"workspace_id": "workspace-a"}
        response = client.post(url, headers=headers, json={"context": body} if shape == "context" else body)
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("mutate_principal", [False, True])
def test_host_scope_hook_cannot_replace_authenticated_persistence_identity(http_runtime, mutate_principal):
    client = http_runtime.client()

    def select_scope(**kwargs):
        if mutate_principal:
            kwargs["principal"].user_id = "user-a"
            kwargs["principal"].workspace_id = "workspace-a"
        return {**kwargs["requested_scope"], "user_id": "user-a", "workspace_id": "workspace-a"}

    http_runtime.hooks.register_bundle({"module_scope_resolver": select_scope}, source="test-host")
    response = client.post("/api/modules/tasks/create_task", headers=http_runtime.token("user-b"),
                           json={"task_id": "b", "title": "B task"})
    assert response.status_code == 200, response.text
    assert client.get("/api/modules/tasks/list_tasks", headers=http_runtime.token("user-a")).json()["items"] == []
    mine = client.get("/api/modules/tasks/list_tasks", headers=http_runtime.token("user-b")).json()["items"]
    assert mine[0]["created_by"] == "user-b"


@pytest.mark.parametrize("ownerless", [False, True])
def test_app_wide_and_ownerless_contracts_preserve_shared_access(http_runtime, ownerless):
    client = http_runtime.client("app_wide", ownerless=ownerless)
    response = client.post("/api/modules/tasks/create_task", headers=http_runtime.token("user-a"),
                           json={"task_id": "shared", "title": "Shared task"})
    assert response.status_code == 200, response.text
    response = client.get("/api/modules/tasks/list_tasks?workspace_id=selected-workspace", headers=http_runtime.token("user-b"))
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["task_id"] == "shared"


def test_dev_persona_cannot_grant_authenticated_ownership(http_runtime, monkeypatch):
    client = http_runtime.client()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_PROVIDER", "none")
    response = client.get("/api/modules/tasks/list_tasks", headers={
        "X-Mozaiks-Dev-User-Id": "user-a", "X-Mozaiks-Dev-Roles": "admin",
    })
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
@pytest.mark.parametrize("surface", ["panels", "tabs", "pages", "relationships"])
def test_profile_hydration_uses_authenticated_owner(http_runtime, monkeypatch, tenancy, surface):
    from mozaiksai.hosts import platform

    client = http_runtime.client(tenancy)
    user_a = http_runtime.token("user-a", "workspace-a")
    user_b = http_runtime.token("user-b", "workspace-b")
    response = client.post("/api/modules/tasks/create_task", headers=user_a,
                           json={"task_id": "a", "title": "A task"})
    assert response.status_code == 200, response.text
    monkeypatch.setattr(platform, "executor_registry", client.app.state.executor_registry)
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: http_runtime.hooks)
    monkeypatch.setattr(platform.app.state, "subscriptions_config", None)
    relationship = surface == "relationships"
    loader = "load_relationship_providers" if relationship else f"load_profile_{surface}"
    action = "relationships" if relationship else "list_tasks"
    monkeypatch.setattr(platform, loader, lambda *_: [{"id": "owned", "module_id": "tasks", "action": action}])
    handler = platform.get_current_user_relationships if relationship else getattr(platform, f"get_profile_{surface}")
    client.app.add_api_route("/profile", handler, methods=["GET"])
    for headers, expected in ((user_a, ["a"]), (user_b, [])):
        response = client.get("/profile", headers=headers)
        assert response.status_code == 200, response.text
        if relationship:
            assert response.json()["providers"][0]["error"] is None
            assert [row["resource_id"] for row in response.json()[surface]] == expected
        else:
            panel = next(row for row in response.json()[surface] if row["id"] == "owned")
            assert panel["error"] is None
            assert [row["task_id"] for row in panel["data"]["items"]] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["user-b", None])
@pytest.mark.parametrize("request_app_id", ["ownership-test-app", ""])
async def test_supplied_context_cannot_replace_request_owned_persistence(http_runtime, identity, request_app_id):
    from mozaiksai.core.runtime.composition.module_context import ModuleContext
    from mozaiksai.core.runtime.composition.module_executor import ModuleRequest
    from mozaiksai.core.runtime.persistence import MongoPersistenceContext, PersistencePrincipal
    from tests.module_authority_test_helpers import trusted_framework_authority

    client = http_runtime.client()
    response = client.post("/api/modules/tasks/create_task", headers=http_runtime.token("user-a"),
                           json={"task_id": "a", "title": "A task"})
    assert response.status_code == 200, response.text
    unscoped = MongoPersistenceContext(app_id="ownership-test-app", user_id="user-a")
    assert (await unscoped.collection("tasks", "tasks").find_one({}))["title"] == "A task"
    supplied = ModuleContext(app_id="ownership-test-app", user_id="user-a", persistence=unscoped)
    executor = client.app.state.executor_registry.module_executor
    result = await executor.execute(ModuleRequest(
        module="tasks", action="custom_list", app_id=request_app_id,
        user_id=identity, authority=trusted_framework_authority(),
        persistence_principal=PersistencePrincipal(user_id=identity) if identity else None,
    ), context=supplied)
    assert supplied.persistence is not unscoped
    if not request_app_id:
        assert supplied.persistence is None
        assert result.success is False
    elif identity:
        assert result.success is True
        assert result.data == {"items": []}
    else:
        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"


@pytest.mark.asyncio
@pytest.mark.parametrize("has_contract", [False, True])
async def test_custom_persistence_requires_executor_without_contract(http_runtime, has_contract):
    from mozaiksai.core.runtime.composition.module_context import ModuleContext
    from mozaiksai.core.runtime.composition.module_executor import ModuleRequest
    from mozaiksai.core.runtime.persistence import MongoPersistenceContext
    from tests.module_authority_test_helpers import trusted_framework_authority

    client = http_runtime.client("app_wide", ownerless=True)
    executor = client.app.state.executor_registry.module_executor
    if not has_contract:
        executor._data_contract = None
    custom = MongoPersistenceContext(app_id="custom-app")
    supplied = ModuleContext(app_id="custom-app", persistence=custom)
    result = await executor.execute(ModuleRequest(
        module="tasks", action="custom_list", app_id="ownership-test-app",
        authority=trusted_framework_authority(),
    ), context=supplied)
    assert result.success is True
    assert (supplied.persistence is custom) is not has_contract
