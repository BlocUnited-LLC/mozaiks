"""Adversarial integration proofs for distributed same-chat exclusion.

These tests run against a real MongoDB (the CI `mongo:7` service, or any
server reachable through MONGO_URI) and prove the cross-instance properties
that in-memory objects cannot: atomic acquisition under contention, renewal,
stale-owner safety, lease-loss write refusal, and true cross-process
exclusion via a subprocess holder.

Skipped when no reachable MongoDB is configured.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from mozaiksai.core.core_config import close_mongo_client
from mozaiksai.core.runtime.persistence import distributed_lock as dl

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _mongo_uri() -> str | None:
    for name in ("MONGO_URI", "MONGODB_URI", "MONGO_URL"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def _mongo_reachable() -> bool:
    uri = _mongo_uri()
    if not uri:
        return False
    try:
        from pymongo import MongoClient

        MongoClient(uri, serverSelectionTimeoutMS=2000).admin.command("ping")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _mongo_reachable(),
    reason="requires a reachable MongoDB via MONGO_URI (provided by the CI mongo service)",
)


@pytest.fixture(autouse=True)
def _required_mode_with_fresh_client(monkeypatch):
    """Bind the Mongo client to this test's event loop and pin required mode."""
    monkeypatch.setenv("MONGO_URI", _mongo_uri() or "")
    close_mongo_client()
    dl.reset_chat_lock_state()
    dl.configure_chat_lock(dl.ChatLockMode.REQUIRED)
    yield
    dl.reset_chat_lock_state()
    close_mongo_client()


def _ids() -> tuple[str, str]:
    tag = uuid4().hex[:10]
    return f"app-{tag}", f"chat-{tag}"


async def _cleanup_resource(resource: str) -> None:
    collection = dl._get_lock_collection()
    if collection is not None:
        await collection.delete_many({"resource": resource})


# ---------------------------------------------------------------------------
# 1. Atomic acquisition: many racers, exactly one owner
# ---------------------------------------------------------------------------

async def test_concurrent_racers_produce_exactly_one_owner(monkeypatch) -> None:
    monkeypatch.setenv("DISTRIBUTED_LOCK_MAX_RETRIES", "0")
    app_id, chat_id = _ids()
    resource = dl.chat_lock_resource(app_id, chat_id)

    gate = asyncio.Event()
    outcomes: list[str] = []

    async def racer(tag: str) -> None:
        try:
            async with dl.chat_execution_lease(app_id=app_id, chat_id=chat_id):
                outcomes.append(f"owner:{tag}")
                await gate.wait()
        except dl.LockAcquisitionError:
            outcomes.append(f"busy:{tag}")

    async def _wait_for_settled() -> None:
        # One owner inside the lease, every other racer rejected as busy.
        while not (
            sum(o.startswith("owner:") for o in outcomes) == 1
            and sum(o.startswith("busy:") for o in outcomes) == 7
        ):
            assert sum(o.startswith("owner:") for o in outcomes) <= 1, outcomes
            await asyncio.sleep(0.01)

    try:
        tasks = [asyncio.create_task(racer(str(i))) for i in range(8)]
        await asyncio.wait_for(_wait_for_settled(), timeout=30)
        gate.set()
        await asyncio.gather(*tasks)
        assert sum(o.startswith("owner:") for o in outcomes) == 1, outcomes
    finally:
        gate.set()
        await _cleanup_resource(resource)


# ---------------------------------------------------------------------------
# 2. Bridge-level race: the loser performs no session/workflow/WAL mutation
# ---------------------------------------------------------------------------

async def test_bridge_race_on_real_lock_loser_mutates_nothing(monkeypatch) -> None:
    from tests.test_chat_execution_lease import (
        _ag2_mod,
        _bridge_mod,
        _DummyTransport,
        _FakeAdapter,
        _FakePersistenceManager,
    )

    monkeypatch.setenv("DISTRIBUTED_LOCK_MAX_RETRIES", "0")
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _name: {})

    app_id, chat_id = _ids()
    resource = dl.chat_lock_resource(app_id, chat_id)
    winner_pm, loser_pm = _FakePersistenceManager(), _FakePersistenceManager()
    winner, loser = _DummyTransport(winner_pm), _DummyTransport(loser_pm)
    adapter = _FakeAdapter(delay=0.6)
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)

    try:
        winner_task = asyncio.create_task(
            winner.handle_user_input_from_api(
                chat_id=chat_id, user_id="u1", workflow_name="AppGenerator",
                message="go", app_id=app_id,
            )
        )
        # Wait until the winner holds the real Mongo lease.
        collection = dl._get_lock_collection()
        assert collection is not None
        for _ in range(200):
            if await collection.find_one({"resource": resource}):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("winner never acquired the Mongo lease")

        loser_result = await loser.handle_user_input_from_api(
            chat_id=chat_id, user_id="u2", workflow_name="AppGenerator",
            message="me too", app_id=app_id,
        )
        winner_result = await winner_task

        assert winner_result["status"] == "success"
        assert loser_result["status"] == "busy"
        assert loser_pm.pending_lookups == []
        assert loser_pm.pending_clears == []
        assert loser_pm.run_user_messages == []
        assert loser.persisted_messages == []
        assert len(adapter.run_requests) == 1
        # Release landed at the terminal boundary: the lease doc is gone.
        assert await collection.find_one({"resource": resource}) is None
    finally:
        await _cleanup_resource(resource)


# ---------------------------------------------------------------------------
# 3. Cross-process exclusion: a subprocess holder blocks this process
# ---------------------------------------------------------------------------

_SUBPROCESS_HOLDER = """
import asyncio, sys

from mozaiksai.core.runtime.persistence import distributed_lock as dl

async def main():
    dl.configure_chat_lock(dl.ChatLockMode.REQUIRED)
    async with dl.chat_execution_lease(
        app_id=sys.argv[1], chat_id=sys.argv[2], holder_id="subprocess-holder"
    ):
        print("ACQUIRED", flush=True)
        await asyncio.sleep(float(sys.argv[3]))
    print("RELEASED", flush=True)

asyncio.run(main())
"""


async def test_cross_process_holder_excludes_this_process(monkeypatch) -> None:
    monkeypatch.setenv("DISTRIBUTED_LOCK_MAX_RETRIES", "0")
    app_id, chat_id = _ids()
    resource = dl.chat_lock_resource(app_id, chat_id)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", _SUBPROCESS_HOLDER, app_id, chat_id, "30",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(_REPO_ROOT),
        env=env,
    )
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=120)
        assert line.strip() == b"ACQUIRED", (
            line,
            (await proc.stderr.read())[:2000],
        )
        with pytest.raises(dl.LockAcquisitionError):
            async with dl.chat_execution_lease(app_id=app_id, chat_id=chat_id):
                pytest.fail("second process must not acquire a held lease")
        # A different chat under the same tenant is unaffected by the holder.
        async with dl.chat_execution_lease(app_id=app_id, chat_id=f"{chat_id}-other"):
            pass
    finally:
        proc.kill()
        await proc.wait()
        await _cleanup_resource(resource)
        await _cleanup_resource(dl.chat_lock_resource(app_id, f"{chat_id}-other"))


# ---------------------------------------------------------------------------
# 4. Independence: distinct chats and distinct tenants proceed concurrently
# ---------------------------------------------------------------------------

async def test_distinct_chats_and_tenants_hold_leases_concurrently() -> None:
    app_id, chat_id = _ids()
    other_app = f"{app_id}-b"
    resources = [
        dl.chat_lock_resource(app_id, chat_id),
        dl.chat_lock_resource(app_id, f"{chat_id}-2"),
        dl.chat_lock_resource(other_app, chat_id),
    ]
    try:
        async with (
            dl.chat_execution_lease(app_id=app_id, chat_id=chat_id),
            dl.chat_execution_lease(app_id=app_id, chat_id=f"{chat_id}-2"),
            dl.chat_execution_lease(app_id=other_app, chat_id=chat_id),
        ):
            collection = dl._get_lock_collection()
            held = [await collection.find_one({"resource": r}) for r in resources]
            assert all(doc is not None for doc in held)
            # Same chat id under different tenants produced distinct leases.
            assert held[0]["resource"] != held[2]["resource"]
    finally:
        for r in resources:
            await _cleanup_resource(r)


# ---------------------------------------------------------------------------
# 5. Renewal preserves ownership across an operation longer than the TTL
# ---------------------------------------------------------------------------

async def test_renewal_preserves_ownership_beyond_ttl(monkeypatch) -> None:
    monkeypatch.setenv("DISTRIBUTED_LOCK_TTL_SECONDS", "2")
    monkeypatch.setenv("DISTRIBUTED_LOCK_MAX_RETRIES", "0")
    app_id, chat_id = _ids()
    resource = dl.chat_lock_resource(app_id, chat_id)

    try:
        async with dl.chat_execution_lease(app_id=app_id, chat_id=chat_id) as lease:
            # Hold well past the 2s TTL; renewal must keep the lease alive.
            for _ in range(3):
                await asyncio.sleep(1.5)
                with pytest.raises(dl.LockAcquisitionError):
                    async with dl.chat_execution_lease(app_id=app_id, chat_id=chat_id):
                        pytest.fail("contender must not steal a renewed lease")
            assert not lease.lost
    finally:
        await _cleanup_resource(resource)


# ---------------------------------------------------------------------------
# 6. A stale owner can never release a successor's lease
# ---------------------------------------------------------------------------

async def test_stale_owner_cannot_release_successor_lease() -> None:
    app_id, chat_id = _ids()
    resource = dl.chat_lock_resource(app_id, chat_id)
    collection = dl._get_lock_collection()
    assert collection is not None

    try:
        assert await dl._try_acquire(collection, resource, "holder-old", 1)
        await asyncio.sleep(1.1)  # let the old lease expire
        assert await dl._try_acquire(collection, resource, "holder-new", 60)

        stale = dl.ChatLease(
            resource=resource,
            holder_id="holder-old",
            mode=dl.ChatLockMode.REQUIRED,
            collection=collection,
            ttl_seconds=60,
        )
        await stale.release()

        doc = await collection.find_one({"resource": resource})
        assert doc is not None, "successor's lease must survive a stale release"
        assert doc["holder_id"] == "holder-new"
    finally:
        await _cleanup_resource(resource)


# ---------------------------------------------------------------------------
# 7. Confirmed lease loss refuses further durable session/WAL writes
# ---------------------------------------------------------------------------

async def test_lease_loss_cancels_protected_execution(monkeypatch) -> None:
    monkeypatch.setenv("DISTRIBUTED_LOCK_TTL_SECONDS", "2")
    from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager

    app_id, chat_id = _ids()
    resource = dl.chat_lock_resource(app_id, chat_id)
    pm = AG2PersistenceManager()
    captured_lease: dl.ChatLease | None = None
    version_healthy: int | None = None
    continued_after_loss = False

    try:
        await pm.create_chat_session(
            chat_id=chat_id, app_id=app_id, workflow_name="AppGenerator", user_id="u1"
        )
        version_before = await pm.get_session_version(chat_id, app_id)

        async def _protected_execution() -> None:
            nonlocal captured_lease, continued_after_loss, version_healthy
            async with dl.chat_execution_lease(app_id=app_id, chat_id=chat_id) as lease:
                captured_lease = lease
                # Writes are allowed while the lease is healthy.
                await pm.append_run_user_message(
                    chat_id=chat_id, app_id=app_id, content="healthy write"
                )
                assert len(await pm.load_run_history(chat_id=chat_id, app_id=app_id)) == 1
                await pm.clear_pending_input_request(chat_id=chat_id, app_id=app_id)
                version_healthy = await pm.get_session_version(chat_id, app_id)
                assert version_healthy == (version_before or 0) + 1

                # Simulate expiry/takeover. The renewal task must cancel this
                # protected owner rather than merely setting a diagnostic bit.
                collection = dl._get_lock_collection()
                await collection.delete_many({"resource": resource})
                await asyncio.Event().wait()
                continued_after_loss = True

        with pytest.raises(dl.ChatLeaseLostError):
            await asyncio.wait_for(_protected_execution(), timeout=10)

        assert captured_lease is not None and captured_lease.lost
        assert not continued_after_loss
        assert await pm.get_session_version(chat_id, app_id) == version_healthy
        history = await pm.load_run_history(chat_id=chat_id, app_id=app_id)
        assert len(history) == 1
    finally:
        coll = await pm._coll()
        await coll.delete_one({"_id": chat_id})
        from mozaiksai.core.core_config import get_mongo_client
        from mozaiksai.core.data.persistence.namespaces import (
            SYSTEM_DATABASE,
            RuntimeCollections,
        )

        client = get_mongo_client()
        await client[SYSTEM_DATABASE][RuntimeCollections.AG2_STREAM_EVENTS].delete_many(
            {"app_id": app_id}
        )
        await client[SYSTEM_DATABASE][RuntimeCollections.AG2_STREAM_HEADS].delete_many(
            {"app_id": app_id}
        )
        await _cleanup_resource(resource)


# ---------------------------------------------------------------------------
# 8. Exceptions inside the protected section release the lease
# ---------------------------------------------------------------------------

async def test_exception_inside_lease_releases_lock() -> None:
    app_id, chat_id = _ids()
    resource = dl.chat_lock_resource(app_id, chat_id)
    collection = dl._get_lock_collection()
    assert collection is not None

    try:
        with pytest.raises(RuntimeError):
            async with dl.chat_execution_lease(app_id=app_id, chat_id=chat_id):
                assert await collection.find_one({"resource": resource}) is not None
                raise RuntimeError("boom")
        assert await collection.find_one({"resource": resource}) is None
        # Immediately re-acquirable.
        async with dl.chat_execution_lease(app_id=app_id, chat_id=chat_id):
            pass
    finally:
        await _cleanup_resource(resource)


# ---------------------------------------------------------------------------
# 9. Cancellation of the protected task still releases the lease
# ---------------------------------------------------------------------------

async def test_cancellation_releases_lease() -> None:
    app_id, chat_id = _ids()
    resource = dl.chat_lock_resource(app_id, chat_id)
    collection = dl._get_lock_collection()
    assert collection is not None
    entered = asyncio.Event()

    async def holder() -> None:
        async with dl.chat_execution_lease(app_id=app_id, chat_id=chat_id):
            entered.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(holder())
    try:
        await asyncio.wait_for(entered.wait(), timeout=30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The detached release completes shortly after cancellation.
        for _ in range(100):
            if await collection.find_one({"resource": resource}) is None:
                break
            await asyncio.sleep(0.05)
        assert await collection.find_one({"resource": resource}) is None
    finally:
        await _cleanup_resource(resource)
