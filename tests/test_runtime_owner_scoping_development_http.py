"""Opt-in local-development ownership acceptance over TCP and real Mongo."""
from __future__ import annotations

import os
import socket
import threading
import time
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from mozaiksai.core.auth.adapters import AuthError
from mozaiksai.core.auth.adapters.no_auth import NoAuthAdapter
from tests import test_runtime_owner_scoping_http as ownership_http

http_runtime = ownership_http.http_runtime

pytestmark = pytest.mark.skipif(
    os.getenv("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1",
    reason="set MOZAIKS_RUN_REAL_MONGO_TESTS=1 for real Mongo development HTTP acceptance",
)


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
@pytest.mark.parametrize("environment", ["development", "production"])
def test_development_ownership_over_tcp_and_real_mongo(http_runtime, monkeypatch, tenancy, environment):
    uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    database_name = f"mozaiks_owner_dev_http_test_{uuid4().hex}"
    monkeypatch.setenv("MOZAIKS_APP_DATABASE_NAME", database_name)
    monkeypatch.setenv("ENV", environment)
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_PROVIDER", "none")
    monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", NoAuthAdapter)
    clients = []

    def mongo_client():
        if not clients:
            clients.append(AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000))
        return clients[0]

    monkeypatch.setattr("mozaiksai.core.runtime.persistence.mongo.get_mongo_client", mongo_client)
    app = http_runtime.client(tenancy).app

    @app.exception_handler(AuthError)
    async def auth_configuration_error(_request, exc):
        # An unhandled ASGI error sends 500 then closes the socket; Windows
        # can reset it before HTTPX receives that response. Handle this known
        # configuration rejection so the actual status and reason are tested.
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="critical", access_log=False, lifespan="off",
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "The local development acceptance HTTP server did not start"
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            url = "/api/modules/tasks/"
            headers = {"X-Mozaiks-Dev-User-Id": "user-a"}
            created = client.post(url + "create_task", headers=headers, json={
                "task_id": "a", "title": "Development task",
            })
            if environment == "production":
                assert created.status_code == 500, created.text
                assert created.json()["detail"].startswith(
                    "Authentication-disabled operation is not permitted in the 'production' environment."
                )
                with MongoClient(uri, serverSelectionTimeoutMS=3000) as verify:
                    assert verify[database_name].list_collection_names() == []
                return
            assert created.status_code == 200, created.text
            response = client.get(url + "list_tasks?workspace_id=forged-workspace", headers=headers)
            assert response.status_code == 200, response.text
            row = response.json()["items"][0]
            owner_field, owner_id = ("created_by", "user-a") if tenancy == "per_user" else ("workspace_owner", "development")
            assert row[owner_field] == owner_id
            assert row["task_id"] == "a"
            response = client.get(url + "custom_list", headers={"X-Mozaiks-Dev-User-Id": "user-b"})
            assert response.status_code == 200, response.text
            assert len(response.json()["items"]) == (0 if tenancy == "per_user" else 1)
            with MongoClient(uri, serverSelectionTimeoutMS=3000) as verify:
                names = verify[database_name].list_collection_names()
                assert len(names) == 1
                assert verify[database_name][names[0]].find_one({"task_id": "a"})[owner_field] == owner_id
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        for mongo in clients:
            mongo.close()
        assert database_name.startswith("mozaiks_owner_dev_http_test_")
        with MongoClient(uri, serverSelectionTimeoutMS=3000) as cleanup:
            cleanup.drop_database(database_name)
