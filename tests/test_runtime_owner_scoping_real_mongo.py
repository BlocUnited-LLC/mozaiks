"""Opt-in ownership acceptance over TCP HTTP, signed JWTs, and local Mongo."""
from __future__ import annotations

import os
import socket
import threading
import time
from uuid import uuid4

import httpx
import pytest
import uvicorn
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from mozaiksai.core.runtime.persistence import MongoPersistenceContext, PersistencePrincipal
from tests import test_runtime_owner_scoping_http as ownership_http

http_runtime = ownership_http.http_runtime

pytestmark = pytest.mark.skipif(
    os.getenv("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1",
    reason="set MOZAIKS_RUN_REAL_MONGO_TESTS=1 for real Mongo ownership HTTP acceptance",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("tenancy,identity", [("per_user", "user-a"), ("per_workspace", "workspace-a")])
async def test_real_upsert_stamps_owner_and_never_matches_legacy_owner_arrays(tenancy, identity):
    database_name = f"mozaiks_owner_upsert_test_{uuid4().hex}"
    client = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=3000)
    try:
        context = MongoPersistenceContext(
            app_id="ownership-test-app", client=client, database_name=database_name,
            data_contract=ownership_http._contract(tenancy),
            principal=PersistencePrincipal("user-a", "workspace-a"),
        )
        collection = context.collection("tasks", "tasks")
        owner_field = "created_by" if tenancy == "per_user" else "workspace_owner"
        result = await collection.update_one({"task_id": "new"}, {"$set": {"title": "new"}}, upsert=True)
        assert result.upserted_id is not None
        assert (await collection.find_one({"task_id": "new"}))[owner_field] == identity
        raw = client[database_name][context.collection_name("tasks", "tasks")]
        await raw.insert_one({"app_id": "ownership-test-app", "task_id": "old", owner_field: [identity, "victim"]})
        result = await collection.update_one({"task_id": "old"}, {"$set": {"title": "owned"}}, upsert=True)
        assert result.matched_count == 0 and result.upserted_id is not None
        assert await raw.count_documents({"task_id": "old"}) == 2
        assert (await collection.find_one({"task_id": "old"}))[owner_field] == identity
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.parametrize("tenancy,owner_field", [
    ("per_user", "created_by"), ("per_workspace", "workspace_owner"),
])
def test_signed_owners_are_isolated_over_tcp_and_real_mongo(
    http_runtime, monkeypatch, tenancy, owner_field,
):
    database_name = f"mozaiks_owner_http_test_{uuid4().hex}"
    uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    monkeypatch.setenv("MOZAIKS_APP_DATABASE_NAME", database_name)
    clients = []

    def mongo_client():
        # Construct on the HTTP event loop, which owns every persistence call.
        if not clients:
            clients.append(AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000))
        return clients[0]

    monkeypatch.setattr("mozaiksai.core.runtime.persistence.mongo.get_mongo_client", mongo_client)
    app = http_runtime.client(tenancy).app
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", access_log=False, lifespan="off",
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "The local ownership acceptance HTTP server did not start"
        user_a = http_runtime.token("user-a", "workspace-a")
        user_b = http_runtime.token("user-b", "workspace-b")
        owner_a = "user-a" if tenancy == "per_user" else "workspace-a"
        owner_b = "user-b" if tenancy == "per_user" else "workspace-b"

        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            url = "/api/modules/tasks/"
            for headers, task_id in ((user_a, "a"), (user_b, "b")):
                response = client.post(url + "create_task", headers=headers, json={
                    "task_id": task_id, "title": f"Owner {task_id}",
                })
                assert response.status_code == 200, response.text

            for headers, task_id, owner in ((user_a, "a", owner_a), (user_b, "b", owner_b)):
                for action in ("list_tasks", "custom_list"):
                    response = client.get(url + action, headers=headers)
                    assert response.status_code == 200, response.text
                    items = response.json()["items"]
                    assert len(items) == 1
                    assert items[0]["task_id"] == task_id
                    assert items[0][owner_field] == owner
                response = client.get(url + "task_summary", headers=headers)
                assert response.status_code == 200, response.text
                assert response.json() == {"rows": [{"_id": None, "count": 1}]}

            response = client.get(url + "get_tasks?id=a", headers=user_b)
            assert response.status_code == 404, response.text
            assert "Owner a" not in response.text
            assert client.get(url + "custom_read?task_id=a", headers=user_b).json() == {"item": None}
            assert client.post(url + "update_task", headers=user_b, json={
                "task_id": "a", "title": "stolen",
            }).json() == {"matched": 0}
            assert client.post(url + "delete_task", headers=user_b, json={"task_id": "a"}).json() == {"deleted": 0}
            own = client.get(url + "custom_read?task_id=a", headers=user_a)
            assert own.json()["item"]["title"] == "Owner a"
            assert own.json()["item"][owner_field] == owner_a

            # Rows written before the boundary existed may contain malformed
            # owners. Mongo scalar equality also matches an array element.
            with MongoClient(uri, serverSelectionTimeoutMS=3000) as seed:
                names = seed[database_name].list_collection_names()
                assert len(names) == 1
                seed[database_name][names[0]].insert_one({
                    "app_id": "ownership-test-app", "task_id": "array-owner", "title": "invalid",
                    owner_field: [owner_a, owner_b],
                })
            for headers in (user_a, user_b):
                assert client.get(url + "custom_read?task_id=array-owner", headers=headers).json() == {"item": None}
                assert client.post(url + "update_task", headers=headers, json={
                    "task_id": "array-owner", "title": "stolen",
                }).json() == {"matched": 0}
                assert client.post(url + "delete_task", headers=headers, json={"task_id": "array-owner"}).json() == {"deleted": 0}

            conflict = client.post(url + "create_task", headers=user_b, json={
                "task_id": "forged", "title": "forged", owner_field: owner_a,
            })
            assert conflict.status_code == 403, conflict.text
            forbidden = client.get(url + "foreign_aggregate", headers=user_b)
            assert forbidden.status_code == 403, forbidden.text
            if tenancy == "per_workspace":
                forged = client.get(url + "list_tasks?workspace_id=workspace-a",
                                    headers=http_runtime.token("user-b"))
                assert forged.status_code == 403, forged.text
            assert client.post(url + "update_task", headers=user_a, json={
                "task_id": "a", "title": "authorized",
            }).json() == {"matched": 1}
            assert client.post(url + "delete_task", headers=user_a, json={"task_id": "a"}).json() == {"deleted": 1}
            assert client.get(url + "list_tasks", headers=user_a).json() == {"items": [], "total": 0}
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        for mongo in clients:
            mongo.close()
        assert database_name.startswith("mozaiks_owner_http_test_")
        with MongoClient(uri, serverSelectionTimeoutMS=3000) as cleanup:
            cleanup.drop_database(database_name)
