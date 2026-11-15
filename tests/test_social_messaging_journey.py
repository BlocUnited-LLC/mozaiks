"""Two signed users exercise materialized OSS packs against isolated real Mongo."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_managed_capability_templates,
)
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from tests.test_generated_messaging_scope_http import runtime as signed_runtime

runtime = signed_runtime

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "sha256:b096b4cb9269f3ebcf363be63f1c50920f786879d03a1890347a3bf33f1f0df0"


@pytest.fixture
def mongo_journey():
    if not shutil.which("node") or not (ROOT / "web_shell/node_modules/esbuild").exists():
        pytest.skip("The browser journey requires Node and web_shell dependencies")
    try:
        available = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    except FileNotFoundError:
        pytest.skip("Docker is required for the real-Mongo journey")
    if available.returncode:
        pytest.skip("The pinned local Mongo image is required for this journey")
    name = "mozaiks-social-proof-" + uuid4().hex[:12]
    container_id = subprocess.check_output([
        "docker", "run", "--detach", "--rm", "--name", name,
        "--label", "codex.owner=" + name, "--publish", "127.0.0.1::27017", IMAGE,
    ], text=True).strip()
    try:
        observed = json.loads(subprocess.check_output(["docker", "inspect", container_id], text=True))[0]
        port = observed["NetworkSettings"]["Ports"]["27017/tcp"][0]["HostPort"]
        uri = f"mongodb://127.0.0.1:{port}"
        with MongoClient(uri, serverSelectionTimeoutMS=30000) as client:
            client.admin.command("ping")
        yield uri
    finally:
        observed = json.loads(subprocess.check_output(["docker", "inspect", container_id], text=True))[0]
        assert observed["Id"] == container_id and observed["Config"]["Labels"]["codex.owner"] == name
        subprocess.run(["docker", "stop", container_id], check=True, capture_output=True)


def test_two_users_friend_request_accept_dm_reply_and_reload(runtime, mongo_journey, tmp_path, monkeypatch):
    executor = runtime.client.app.state.executor_registry.module_executor
    rendered = resolve_managed_capability_templates([{
        "id": "social", "capability_source": "generated_module",
        "pack_source_path": str(ROOT / "factory_app/build_context/social"),
    }])
    for item in rendered:
        if item["filename"].startswith("modules/friends/"):
            target = tmp_path / item["filename"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
    loaded = ModuleLoader(str(tmp_path)).load("friends")
    executor.register_loaded_module(loaded)
    runtime.client.app.state.module_action_surfaces["friends"] = loaded.action_api_surface_map
    social_contract = json.loads((ROOT / "factory_app/build_context/social/templates/data/contract.json").read_text())
    executor._data_contract["surfaces"].extend(social_contract["surfaces"])
    client = AsyncIOMotorClient(mongo_journey)
    executor._persistence_client = client
    executor._persistence_database = "social_messaging_journey"
    from io import BytesIO

    from PIL import Image

    from mozaiksai.core.media import store as media_store
    from mozaiksai.hosts import platform

    monkeypatch.setattr(media_store, "_content_store", media_store.LocalMediaContentStore(root=tmp_path / "media"))

    async def profiles():
        return executor._persistence_client["social_messaging_journey"]["UserProfiles"]

    monkeypatch.setattr(platform, "_account_profile_collection", profiles)
    for route in platform.app.routes:
        if getattr(route, "path", "").startswith(("/api/me/media/", "/api/users/{user_id}/media/")):
            runtime.client.app.add_api_route(route.path, route.endpoint, methods=list(route.methods))
    scopes = "messages.read messages.write friends.read friends.manage"

    def action(user, module, name, **params):
        response = runtime.client.post(f"/api/modules/{module}/{name}", json={"params": params},
                                      headers=runtime.token(user=user, scopes=scopes))
        assert response.status_code == 200, response.text
        return response.json()

    from tests.test_messaging_pack_persistence_composition import _assembled_files

    for item in rendered:
        if item["filename"].startswith("ui/"):
            target = tmp_path / item["filename"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
    for filename, content in _assembled_files().items():
        if filename.startswith("ui/") and filename != "ui/index.js":
            target = tmp_path / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    from factory_app.workflows.AppGenerator.tools.module_api_template import get_module_api_template

    helper = tmp_path / "ui/lib/moduleApi.js"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text(get_module_api_template(), encoding="utf-8")

    try:
        with runtime.client:
            from mozaiksai.core.runtime.persistence import (
                apply_data_migrations,
                load_data_migrations,
            )
            from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceContext

            async def migrate():
                context = MongoPersistenceContext(app_id="generated-messaging-app", client=client,
                    database_name="social_messaging_journey", data_contract=executor._data_contract)
                return await apply_data_migrations(app_id="generated-messaging-app",
                    migrations=load_data_migrations(ROOT / "factory_app/build_context/social/templates"),
                    persistence=context, history_client=client, history_database="social_messaging_journey")

            assert runtime.client.portal.call(migrate) == 1
            source = BytesIO()
            Image.new("RGB", (32, 16), "teal").save(source, "PNG")
            for kind in ["avatar", "cover"]:
                upload = runtime.client.put(f"/api/me/media/{kind}", content=source.getvalue(), headers=runtime.token())
                assert upload.status_code == 200, upload.text
                url = upload.json()[f"{kind}_url"]
                assert runtime.client.get(url).status_code in {401, 403}
                fetched = runtime.client.get(url, headers=runtime.token(user="bob"))
                assert fetched.status_code == 200
                assert fetched.content.startswith(b"\xff\xd8")
            for user in ["bob", "charlie"]:
                assert runtime.client.put("/api/me/media/avatar", content=source.getvalue(),
                    headers=runtime.token(user=user)).status_code == 200
            request = action("alice", "friends", "send_friend_request", recipient_id="bob")
            assert request["success"] is True
            request_id = request["request"]["request_id"]
            assert action("mallory", "friends", "accept_friend_request", request_id=request_id)["success"] is False
            assert action("bob", "friends", "accept_friend_request", request_id=request_id)["success"] is True
            for user, friend in [("alice", "bob"), ("bob", "alice")]:
                assert action(user, "friends", "list_friends")["friends"][0]["friend_user_id"] == friend
            created = action("alice", "messages", "create_thread", participant_ids=["alice", "bob"], thread_type="direct")
            thread_id = created["thread"]["thread_id"]
            assert action("alice", "messages", "send_message", thread_id=thread_id, body="Hello Bob")["success"] is True
            assert action("bob", "messages", "send_message", thread_id=thread_id, body="Hello Alice")["success"] is True
            assert action("mallory", "messages", "get_thread", thread_id=thread_id)["thread"] is None
            assert action("mallory", "messages", "send_message", thread_id=thread_id, body="intrusion")["success"] is False
            # A fresh persistence client re-reads durable records after the writes.
            reloaded = AsyncIOMotorClient(mongo_journey)
            executor._persistence_client = reloaded
            try:
                messages = action("bob", "messages", "get_thread", thread_id=thread_id)["messages"]
                assert {m["body"] for m in messages} == {"Hello Bob", "Hello Alice"}
                assert action("alice", "friends", "list_friends")["friends"][0]["friend_user_id"] == "bob"
            finally:
                reloaded.close()
            # Exercise the materialized UI and canonical moduleApi helper through
            # an actual loopback HTTP server with the same signed test identities.
            import socket
            import threading
            import time

            import uvicorn
            from fastapi import FastAPI
            from fastapi.middleware.cors import CORSMiddleware

            api_app = FastAPI()
            api_app.state = runtime.client.app.state
            api_app.include_router(runtime.client.app.router)
            api_app.add_api_route("/api/users/{username}", platform.get_public_user_profile)
            api_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
            browser_client = AsyncIOMotorClient(mongo_journey)
            executor._persistence_client = browser_client
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            api_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            server = uvicorn.Server(uvicorn.Config(api_app, log_level="error", lifespan="off"))
            thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
            thread.start()
            config_path = tmp_path / "browser-private.json"
            try:
                deadline = time.monotonic() + 15
                while not server.started and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert server.started
                config_path.write_text(json.dumps({"workspace": str(tmp_path), "api": api_url,
                    "tokens": {user: runtime.token(user=user, scopes=scopes)["Authorization"].removeprefix("Bearer ")
                               for user in ["alice", "bob", "charlie"]}}), encoding="utf-8")
                completed = subprocess.run(["node", str(ROOT / "web_shell/scripts/prove-social-journey.mjs"),
                    str(config_path)], capture_output=True, text=True, timeout=120)
                assert completed.returncode == 0, completed.stderr[-3000:]
            finally:
                config_path.unlink(missing_ok=True)
                server.should_exit = True
                thread.join(timeout=15)
                listener.close()
                browser_client.close()
                assert not thread.is_alive()
    finally:
        client.close()
