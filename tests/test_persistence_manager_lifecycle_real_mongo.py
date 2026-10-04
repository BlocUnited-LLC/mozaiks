"""Chat-session persistence facts that only a real Mongo can show.

Two launch-path facts the live smoke runner depends on: a session created
ahead of its first run is started, not resumed, and a long-lived manager keeps
working after a host shutdown closed the process client.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from mozaiksai.core.core_config import close_mongo_client
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager


@pytest.fixture
def mongo_uri() -> str:
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
    return uri


def test_a_session_is_resumable_only_once_its_first_run_has_events(mongo_uri: str) -> None:
    workflow = "ResumeProbe"

    async def scenario() -> tuple[bool, bool, bool]:
        pm = AG2PersistenceManager()
        app_id, chat_id = f"resume-probe-{uuid4().hex[:8]}", f"chat-{uuid4().hex[:8]}"
        await pm.create_chat_session(chat_id, app_id, workflow_name=workflow, user_id="probe-user")
        try:
            created = await pm.chat_session_exists(chat_id, app_id, workflow)
            await pm.append_run_user_message(
                chat_id=chat_id, app_id=app_id, content="first turn", metadata={"source": "workflow_user"},
            )
            ran = await pm.chat_session_exists(chat_id, app_id, workflow)
            await pm.mark_chat_completed(chat_id, app_id)
            completed = await pm.chat_session_exists(chat_id, app_id, workflow)
        finally:
            await (await pm._coll()).delete_many({"app_id": app_id})
        return created, ran, completed

    try:
        assert asyncio.run(scenario()) == (False, True, False)
    finally:
        close_mongo_client()


def test_a_long_lived_manager_rebinds_after_the_process_client_closes(mongo_uri: str) -> None:
    manager = AG2PersistenceManager()

    async def use() -> object:
        await (await manager._coll()).find_one({"_id": "rebind-probe"})
        return manager.persistence.client

    try:
        first = asyncio.run(use())
        close_mongo_client()  # what host shutdown does
        second = asyncio.run(use())  # the next host start runs on a new event loop
    finally:
        close_mongo_client()

    assert second is not first
