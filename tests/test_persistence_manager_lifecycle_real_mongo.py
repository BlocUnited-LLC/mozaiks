"""Chat-session persistence facts that only a real Mongo can show.

Two launch-path facts the live smoke runner depends on: a session created
ahead of its first run is started, not resumed, and a long-lived manager keeps
working after a host shutdown closed the process client. Only a process client
the manager bound itself is rebound; a client a caller supplied is kept.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest

from mozaiksai.core.core_config import close_mongo_client, get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager


@pytest.fixture
def mongo_uri() -> Iterator[str]:
    uri = os.environ.get("MONGO_URI", "").strip()
    if not uri:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MONGO_URI is not set")
    from pymongo import MongoClient

    client: MongoClient = MongoClient(uri, serverSelectionTimeoutMS=2000)
    try:
        client.admin.command("ping")
    except Exception:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MongoDB is unavailable")
    finally:
        client.close()
    # The process client belongs to whichever event loop opened it; an earlier
    # test's client would fail on this test's loop.
    close_mongo_client()
    try:
        yield uri
    finally:
        close_mongo_client()


def test_a_session_is_resumable_only_once_its_first_run_has_events(mongo_uri: str) -> None:
    workflow = "ResumeProbe"

    async def scenario() -> tuple[bool, bool, bool]:
        pm = AG2PersistenceManager()
        app_id, chat_id = f"resume-probe-{uuid4().hex[:8]}", f"chat-{uuid4().hex[:8]}"
        await pm.create_chat_session(chat_id, app_id, workflow_name=workflow, user_id="probe-user")
        try:
            created = await pm.chat_has_resumable_run(chat_id, app_id, workflow)
            await pm.append_run_user_message(
                chat_id=chat_id, app_id=app_id, content="first turn", metadata={"source": "workflow_user"},
            )
            ran = await pm.chat_has_resumable_run(chat_id, app_id, workflow)
            await pm.mark_chat_completed(chat_id, app_id)
            completed = await pm.chat_has_resumable_run(chat_id, app_id, workflow)
        finally:
            await (await pm._coll()).delete_many({"app_id": app_id})
        return created, ran, completed

    assert asyncio.run(scenario()) == (False, True, False)


def test_a_long_lived_manager_rebinds_after_the_process_client_closes(mongo_uri: str) -> None:
    manager = AG2PersistenceManager()

    async def use() -> object:
        await (await manager._coll()).find_one({"_id": "rebind-probe"})
        return manager.persistence.client

    first = asyncio.run(use())
    close_mongo_client()  # what host shutdown does
    second = asyncio.run(use())  # the next host start runs on a new event loop

    assert second is not first


class _CallerDatabaseClient:
    """A caller's client that serves every database name from its own database."""

    def __init__(self, client: Any, database_name: str) -> None:
        self._client = client
        self._database_name = database_name

    def __getitem__(self, _name: str) -> Any:
        return self._client[self._database_name]


def test_a_supplied_client_is_kept_across_a_host_restart(mongo_uri: str) -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    workflow = "SuppliedClientProbe"
    database_name = f"supplied_client_probe_{uuid4().hex[:12]}"
    app_id = f"supplied-probe-{uuid4().hex[:8]}"

    async def scenario() -> tuple[bool, list[str], int]:
        caller_client: Any = AsyncIOMotorClient(mongo_uri)
        supplied = _CallerDatabaseClient(caller_client, database_name)
        manager = AG2PersistenceManager()
        manager.persistence.client = supplied
        try:
            get_mongo_client()  # a running host holds the process client
            await manager.create_chat_session("chat-before", app_id, workflow_name=workflow, user_id="probe-user")
            close_mongo_client()  # host shutdown
            process_client = get_mongo_client()  # the next host start
            await manager.create_chat_session("chat-after", app_id, workflow_name=workflow, user_id="probe-user")
            kept = manager.persistence.client is supplied
            caller_sessions = caller_client[database_name][RuntimeCollections.CHAT_SESSIONS]
            written = sorted([doc["_id"] async for doc in caller_sessions.find({"app_id": app_id}, {"_id": 1})])
            shared_sessions = process_client[SYSTEM_DATABASE][RuntimeCollections.CHAT_SESSIONS]
            leaked = await shared_sessions.count_documents({"app_id": app_id})
        finally:
            await caller_client.drop_database(database_name)
            caller_client.close()
        return kept, written, leaked

    assert asyncio.run(scenario()) == (True, ["chat-after", "chat-before"], 0)
