"""Real-Mongo proofs for the server-owned session field boundary.

The immutable run identity, the run/build binding, and the terminal build
receipt are lifecycle authority: only the privileged, lease-fenced server
write path may install or replace them. Every generic session write path —
creation with extra_fields, model/tool-authored context persistence, and the
replay merge — must reject or drop them with detection, so an injected value
can never reach the persisted session document through a generic API.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

import mozaiksai.core.runtime.persistence.distributed_lock as dl
from mozaiksai.core.data.persistence.persistence_manager import (
    SERVER_OWNED_SESSION_FIELDS,
    AG2PersistenceManager,
    PersistenceManager,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1",
    reason="set MOZAIKS_RUN_REAL_MONGO_TESTS=1 for real Mongo session-field tests",
)

_MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
_APP_ID = "app-server-owned"
_WORKFLOW = "AppGenerator"

_INJECTED = {
    "workflow_run_id": "wfrun_attacker",
    "run_build_binding": {"build_id": "stolen", "forged": True},
    "build_terminal_receipt": {"kind": "success", "forged": True},
}


class _TestDatabaseClient:
    def __init__(self, client: AsyncIOMotorClient, database_name: str) -> None:
        self._client = client
        self._database_name = database_name

    def __getitem__(self, _name: str):
        return self._client[self._database_name]


def _pm(client: AsyncIOMotorClient, database_name: str) -> AG2PersistenceManager:
    pm = AG2PersistenceManager()
    pm.persistence = PersistenceManager()
    pm.persistence.client = _TestDatabaseClient(client, database_name)
    return pm


async def _seed_session(pm: AG2PersistenceManager, chat_id: str) -> None:
    coll = await pm._coll()
    await coll.insert_one(
        {
            "_id": chat_id,
            "chat_id": chat_id,
            "app_id": _APP_ID,
            "workflow_name": _WORKFLOW,
            "user_id": "user-1",
            "status": "active",
            "messages": [],
            "session_version": 1,
        }
    )


def _assert_clean(doc: dict) -> None:
    for key in SERVER_OWNED_SESSION_FIELDS:
        assert key not in doc, f"server-owned field {key!r} leaked into storage"


@pytest.mark.asyncio
async def test_create_chat_session_rejects_server_owned_extra_fields_real_mongo() -> None:
    """Generic creation is a direct caller write: injection is rejected
    outright and no session document is created at all (fail closed)."""
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_boundary_test_{uuid4().hex}"
    chat_id = f"chat_{uuid4().hex}"
    try:
        pm = _pm(client, database_name)
        for key, value in _INJECTED.items():
            with pytest.raises(ValueError, match="server-owned"):
                await pm.create_chat_session(
                    chat_id,
                    app_id=_APP_ID,
                    workflow_name=_WORKFLOW,
                    user_id="user-1",
                    extra_fields={"parent_chat_id": "ok", key: value},
                )
            assert await (await pm._coll()).find_one({"_id": chat_id}) is None
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_create_chat_session_rejects_reserved_fields_for_existing_session_real_mongo() -> None:
    """A request carrying a reserved key is invalid independent of session
    existence: against an existing session it must reject deterministically,
    never return the idempotent success, and must leave the existing document
    byte-equivalent."""
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_boundary_test_{uuid4().hex}"
    chat_id = f"chat_{uuid4().hex}"
    try:
        pm = _pm(client, database_name)
        await pm.create_chat_session(
            chat_id,
            app_id=_APP_ID,
            workflow_name=_WORKFLOW,
            user_id="user-1",
            extra_fields={"parent_chat_id": "chat_parent"},
        )
        before = await (await pm._coll()).find_one({"_id": chat_id})
        assert before is not None

        # Reserved only, and benign + reserved: both reject against the
        # existing session with zero mutation.
        for fields in (
            {"workflow_run_id": "wfrun_attacker"},
            {"journey_key": "genesis", "run_build_binding": {"forged": True}},
        ):
            with pytest.raises(ValueError, match="server-owned"):
                await pm.create_chat_session(
                    chat_id,
                    app_id=_APP_ID,
                    workflow_name=_WORKFLOW,
                    user_id="user-1",
                    extra_fields=fields,
                )
            after = await (await pm._coll()).find_one({"_id": chat_id})
            assert after == before

        # Benign-only against the existing session keeps the idempotent
        # early-return behavior: success, zero mutation.
        await pm.create_chat_session(
            chat_id,
            app_id=_APP_ID,
            workflow_name=_WORKFLOW,
            user_id="user-1",
            extra_fields={"journey_key": "genesis"},
        )
        after = await (await pm._coll()).find_one({"_id": chat_id})
        assert after == before
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_create_chat_session_benign_extra_fields_unaffected_real_mongo() -> None:
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_boundary_test_{uuid4().hex}"
    chat_id = f"chat_{uuid4().hex}"
    try:
        pm = _pm(client, database_name)
        await pm.create_chat_session(
            chat_id,
            app_id=_APP_ID,
            workflow_name=_WORKFLOW,
            user_id="user-1",
            extra_fields={"parent_chat_id": "chat_parent", "journey_key": "genesis"},
        )
        doc = await (await pm._coll()).find_one({"_id": chat_id})
        assert doc is not None
        assert doc["parent_chat_id"] == "chat_parent"
        assert doc["journey_key"] == "genesis"
        _assert_clean(doc)
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_generic_context_persistence_drops_server_owned_fields_real_mongo() -> None:
    """Model/tool-authored context updates flow through persist_context_variables:
    reserved keys are dropped with detection and never reach the document."""
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_boundary_test_{uuid4().hex}"
    chat_id = f"chat_{uuid4().hex}"
    try:
        pm = _pm(client, database_name)
        await _seed_session(pm, chat_id)

        await pm.persist_context_variables(
            chat_id=chat_id,
            app_id=_APP_ID,
            workflow_name=_WORKFLOW,
            variables={**_INJECTED, "app_download_ready": True},
        )
        # Dotted variants of reserved keys are undeclared context variables:
        # the workflow-declared persistence policy drops them, so they can
        # never materialize or mutate a reserved field through nesting.
        await pm.persist_context_variables(
            chat_id=chat_id,
            app_id=_APP_ID,
            workflow_name=_WORKFLOW,
            variables={"workflow_run_id.nested": "x", "run_build_binding.build_id": "y"},
        )

        doc = await (await pm._coll()).find_one({"_id": chat_id})
        assert doc is not None
        _assert_clean(doc)
        assert "workflow_run_id.nested" not in doc
        assert "run_build_binding.build_id" not in doc

        fetched = await pm.fetch_chat_session_extra_context(
            chat_id=chat_id, app_id=_APP_ID, workflow_name=_WORKFLOW
        )
        _assert_clean(fetched)
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_privileged_setter_writes_and_replay_returns_real_mongo() -> None:
    """The privileged writer is the single durable path; the replay merge
    preserves server-owned fields past workflow-declared context policy."""
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_boundary_test_{uuid4().hex}"
    chat_id = f"chat_{uuid4().hex}"
    try:
        pm = _pm(client, database_name)
        await _seed_session(pm, chat_id)

        receipt = {"kind": "failure", "workflow_run_id": "wfrun_legit"}
        binding = {"workflow_run_id": "wfrun_legit", "build_id": "build_wfrun_legit"}
        await pm.persist_server_owned_session_fields(
            chat_id=chat_id,
            app_id=_APP_ID,
            workflow_name=_WORKFLOW,
            fields={
                "workflow_run_id": "wfrun_legit",
                "build_terminal_receipt": receipt,
                "run_build_binding": binding,
            },
        )

        fetched = await pm.fetch_chat_session_extra_context(
            chat_id=chat_id, app_id=_APP_ID, workflow_name=_WORKFLOW
        )
        assert fetched.get("workflow_run_id") == "wfrun_legit"
        assert fetched.get("build_terminal_receipt") == receipt
        assert fetched.get("run_build_binding") == binding
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_privileged_setter_rejects_non_reserved_keys_real_mongo() -> None:
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_boundary_test_{uuid4().hex}"
    chat_id = f"chat_{uuid4().hex}"
    try:
        pm = _pm(client, database_name)
        await _seed_session(pm, chat_id)
        with pytest.raises(ValueError, match="not a server-owned session field"):
            await pm.persist_server_owned_session_fields(
                chat_id=chat_id,
                app_id=_APP_ID,
                workflow_name=_WORKFLOW,
                fields={"app_download_ready": True},
            )
        doc = await (await pm._coll()).find_one({"_id": chat_id})
        assert doc is not None
        assert "app_download_ready" not in doc
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_privileged_setter_fails_closed_on_missing_session_real_mongo() -> None:
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_boundary_test_{uuid4().hex}"
    try:
        pm = _pm(client, database_name)
        with pytest.raises(RuntimeError, match="scoped session was not found"):
            await pm.persist_server_owned_session_fields(
                chat_id=f"chat_missing_{uuid4().hex}",
                app_id=_APP_ID,
                workflow_name=_WORKFLOW,
                fields={"workflow_run_id": "wfrun_x"},
            )
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_privileged_setter_is_lease_fenced_real_mongo() -> None:
    """A worker whose chat execution lease was lost to a successor cannot
    replace the successor run's lifecycle authority through the privileged
    writer — the write is refused before touching Mongo."""
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_boundary_test_{uuid4().hex}"
    chat_id = f"chat_{uuid4().hex}"
    resource = dl.chat_lock_resource(_APP_ID, chat_id)
    try:
        pm = _pm(client, database_name)
        await _seed_session(pm, chat_id)

        lease = dl.ChatLease(
            resource=resource,
            holder_id="stale-worker",
            mode=dl.ChatLockMode.REQUIRED,
            collection=None,
            ttl_seconds=60,
        )
        lease._mark_lost("superseded by successor")
        dl._process_leases[resource] = lease

        with pytest.raises(dl.ChatLeaseLostError):
            await pm.persist_server_owned_session_fields(
                chat_id=chat_id,
                app_id=_APP_ID,
                workflow_name=_WORKFLOW,
                fields={"workflow_run_id": "wfrun_stale_takeover"},
            )
        doc = await (await pm._coll()).find_one({"_id": chat_id})
        assert doc is not None
        _assert_clean(doc)
    finally:
        dl._process_leases.pop(resource, None)
        await client.drop_database(database_name)
        client.close()


def test_reserved_key_set_is_exactly_the_lifecycle_authority_fields() -> None:
    assert SERVER_OWNED_SESSION_FIELDS == {
        "build_terminal_receipt",
        "run_build_binding",
        "workflow_run_id",
    }
