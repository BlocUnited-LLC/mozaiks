"""Admission and mutation races against the real store, with fake/real Mongo."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio

from mozaiksai.core.sandbox.preview_store import (
    MongoPreviewStore,
    PreviewCapacityError,
    PreviewLeaseLostError,
    PreviewOperationBusy,
    PreviewRecoveryRequired,
)
from tests.helpers.preview_mongo import FakePreviewDatabase


@pytest_asyncio.fixture(params=["fake", "mongo"])
async def storage(request):
    now = [datetime(2026, 10, 3, tzinfo=UTC)]
    if request.param == "fake":
        yield FakePreviewDatabase(), now
        return
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=1000)
    database_name = f"preview_store_test_{uuid4().hex}"
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MongoDB is unavailable")
    try:
        yield client[database_name], now
    finally:
        assert database_name.startswith("preview_store_test_")
        await client.drop_database(database_name)
        client.close()


def _store(storage):
    database, now = storage
    return MongoPreviewStore(database, now=lambda: now[0])


async def _reserve(store, artifact="artifact", owner="owner", **limits):
    return await store.reserve(
        {"app_id": "host", "user_id": owner, "artifact_id": artifact, "target_app_id": "target",
         "build_registry_id": "build", "provider": "e2b"},
        **({"max_sessions": 2, "max_owner_sessions": 1, "max_pending": 20, "queue_seconds": 15, "ttl_seconds": 300} | limits),
    )


async def _allocate(store, reservation, now, parallel=1):
    return await store.try_allocate(reservation["sandbox_id"], max_parallel_creates=parallel, provider_deadline=now + timedelta(seconds=330))


async def _attach(store, reservation):
    return await store.attach_session(reservation["sandbox_id"], reservation["allocation_token"], {
        "session_id": "provider-" + reservation["sandbox_id"], "status": "running", "preview_url": "https://preview.example",
        "manifest": '{"appId":"target"}', "paths": ["app.json"], "has_requirements": False,
    })


async def test_manager_recovery_keeps_queued_and_unconfirmed_allocation_handles(storage, monkeypatch):
    from mozaiksai.core.sandbox import preview_sessions

    monkeypatch.setattr(preview_sessions, "_utcnow", lambda: storage[1][0])
    store = _store(storage)
    first = await _reserve(store, "previous-artifact")
    await _allocate(store, first, storage[1][0])
    second = await _reserve(store, "newer-artifact")

    def no_provider():
        pytest.fail("Recovery must not contact, create, or stop a provider")

    manager = preview_sessions.ArtifactPreviewSessionManager(
        store=_store(storage), provider_resolver=no_provider,
    )
    identity = dict(app_id="host", user_id="owner", target_app_id="target", build_registry_id="build")
    states = await manager.list_for_build(**identity)
    assert {state.sandbox_id for state in states} == {first["sandbox_id"], second["sandbox_id"]}
    assert {state.phase for state in states} == {"queued", "provisioning"}
    assert all(state.status == "starting" and state.preview_url is None for state in states)

    # Motor returns naive BSON dates by default; the store normalizes queue
    # deadlines before recovery compares them with the manager's UTC clock.
    queued = await store.get(second["sandbox_id"])
    assert queued["queue_deadline"].utcoffset() == timedelta(0)
    storage[1][0] += timedelta(seconds=16)
    pending = {state.sandbox_id: state for state in await manager.list_for_build(**identity)}
    assert pending[first["sandbox_id"]].status == "starting"
    assert pending[second["sandbox_id"]].status == "error"

    storage[1][0] += timedelta(seconds=315)
    expired = await manager.list_for_build(**identity)
    assert {state.sandbox_id for state in expired} == {first["sandbox_id"], second["sandbox_id"]}
    assert all(state.status == "error" and state.preview_url is None for state in expired)
    assert len(await store.list()) == 2


async def test_manager_recovery_after_restart_keeps_running_identity_and_interrupted_handle(storage, monkeypatch):
    from mozaiksai.core.sandbox import preview_sessions

    monkeypatch.setattr(preview_sessions, "_utcnow", lambda: storage[1][0])
    store = _store(storage)
    first = await _reserve(store, "old-artifact", max_owner_sessions=2)
    first = await _allocate(store, first, storage[1][0])
    await _attach(store, first)
    second = await _reserve(store, "failed-artifact", max_owner_sessions=2)
    second = await _allocate(store, second, storage[1][0])
    await _attach(store, second)
    await store.claim_operation(second["sandbox_id"], kind="sync", lease_seconds=5)
    storage[1][0] += timedelta(seconds=6)
    manager = preview_sessions.ArtifactPreviewSessionManager(store=_store(storage))
    states = {state.artifact_id: state for state in await manager.list_for_build(
        app_id="host", user_id="owner", target_app_id="target", build_registry_id="build",
    )}
    assert states["old-artifact"].sandbox_id == first["sandbox_id"]
    assert states["old-artifact"].status == "running"
    assert states["old-artifact"].preview_url == "https://preview.example"
    assert states["failed-artifact"].sandbox_id == second["sandbox_id"]
    assert states["failed-artifact"].status == "error"
    assert states["failed-artifact"].preview_url is None
    assert "interrupted" in states["failed-artifact"].last_error
    assert len(await store.list()) == 2


async def test_cross_worker_deduplication_and_immutable_identity(storage):
    workers = [_store(storage) for _ in range(24)]
    results = await asyncio.gather(*(_reserve(worker) for worker in workers))
    assert len({result["sandbox_id"] for result in results}) == 1
    assert len(await workers[0].list()) == 1
    with pytest.raises(ValueError, match="identity changed"):
        await workers[0].reserve(
            {key: ("other" if key == "target_app_id" else value) for key, value in results[0].items()
             if key in {"app_id", "user_id", "artifact_id", "target_app_id", "build_registry_id", "provider"}},
            max_sessions=2, max_owner_sessions=1, max_pending=20, queue_seconds=15, ttl_seconds=300,
        )


async def test_sealed_reservation_binds_digest_and_image_without_changing_legacy_identity(storage):
    store = _store(storage)
    old = await _reserve(store, "ordinary")
    assert "sealed_archive_sha256" not in old
    identity = {
        "app_id": "host", "user_id": "owner", "artifact_id": "candidate",
        "target_app_id": "target", "build_registry_id": "build", "provider": "docker",
        "sealed_archive_sha256": "sha256:" + "a" * 64,
        "sealed_image_id": "sha256:" + "b" * 64,
    }
    limits = dict(max_sessions=2, max_owner_sessions=1, max_pending=20, queue_seconds=15, ttl_seconds=300)
    reservation = await store.reserve(identity, **limits)
    assert reservation["sealed_archive_sha256"] == identity["sealed_archive_sha256"]
    assert (await store.reserve(identity, **limits))["sandbox_id"] == reservation["sandbox_id"]
    with pytest.raises(ValueError, match="identity changed"):
        await store.reserve({**identity, "sealed_image_id": "sha256:" + "c" * 64}, **limits)
    with pytest.raises(ValueError, match="identity changed"):
        await store.reserve({key: value for key, value in identity.items() if not key.startswith("sealed_")}, **limits)
    with pytest.raises(ValueError, match="exact archive/image"):
        await store.reserve({**identity, "sealed_archive_sha256": "bad"}, **limits)


async def test_atomic_queue_bound_and_configuration_agreement(storage):
    results = await asyncio.gather(*(_reserve(_store(storage), f"artifact-{index}") for index in range(30)), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 20
    assert sum(isinstance(result, PreviewCapacityError) for result in results) == 10
    with pytest.raises(ValueError, match="differs between workers"):
        await _reserve(_store(storage), "new", max_pending=21)


async def test_parallel_creation_global_owner_limits_and_eligible_fifo(storage):
    store = _store(storage)
    now = storage[1][0]
    first = await _reserve(store, "first", "owner-a")
    blocked_owner = await _reserve(store, "blocked-owner", "owner-a")
    second = await _reserve(store, "second", "owner-b")
    third = await _reserve(store, "third", "owner-c")
    racers = await asyncio.gather(*(_allocate(_store(storage), first, now) for _ in range(12)))
    allocated = [result for result in racers if result is not None]
    assert len(allocated) == 1
    assert await _allocate(store, second, now) is None
    await _attach(store, allocated[0])
    assert await _allocate(store, blocked_owner, now) is None
    assert await _allocate(store, third, now) is None
    second_allocated = await _allocate(store, second, now)
    assert second_allocated is not None
    await _attach(store, second_allocated)
    assert await _allocate(store, third, now) is None
    assert sum(item["phase"] == "active" for item in await store.list()) == 2


async def test_queue_expiry_releases_only_unallocated_admission(storage):
    store = _store(storage)
    queued = await _reserve(store)
    storage[1][0] += timedelta(seconds=16)
    with pytest.raises(KeyError):
        await _allocate(store, queued, storage[1][0])
    replacement = await _reserve(store)
    assert replacement["sandbox_id"] != queued["sandbox_id"]
    assert await store.abandon_queued(replacement["sandbox_id"])
    assert await store.list() == []


async def test_uncertain_allocation_remains_capacity_debt_until_provider_deadline(storage):
    store = _store(storage)
    entry = await _allocate(store, await _reserve(store), storage[1][0])
    assert entry is not None
    assert not await store.abandon_queued(entry["sandbox_id"])
    storage[1][0] += timedelta(seconds=100)
    recovered = await _store(storage).get(entry["sandbox_id"])
    assert recovered["phase"] == "provisioning" and recovered["session_id"] is None
    with pytest.raises(PreviewRecoveryRequired):
        await store.release(entry["sandbox_id"], allocation_token=entry["allocation_token"])
    with pytest.raises(PreviewLeaseLostError):
        await store.release(entry["sandbox_id"], allocation_token="stale", provider_absent=True)
    storage[1][0] += timedelta(seconds=231)
    assert await store.release(entry["sandbox_id"], allocation_token=entry["allocation_token"])
    assert await store.get(entry["sandbox_id"]) is None


async def test_restart_recovers_session_and_fences_mutations(storage):
    first, restarted = _store(storage), _store(storage)
    entry = await _allocate(first, await _reserve(first), storage[1][0])
    original = await _attach(first, entry)
    assert (await restarted.get(entry["sandbox_id"]))["session_id"] == original["session_id"]
    token = await first.claim_operation(entry["sandbox_id"], kind="sync", lease_seconds=10)
    with pytest.raises(PreviewOperationBusy):
        await restarted.claim_operation(entry["sandbox_id"], kind="start", lease_seconds=10)
    saved = await first.save(entry["sandbox_id"], {"paths": ["app.json", "brand/logo.png"]}, token)
    assert saved["revision"] >= 2
    assert (await restarted.get(entry["sandbox_id"]))["paths"] == ["app.json", "brand/logo.png"]
    storage[1][0] += timedelta(seconds=11)
    for kind in ("sync", "start", "status"):
        with pytest.raises(PreviewRecoveryRequired):
            await restarted.claim_operation(entry["sandbox_id"], kind=kind, lease_seconds=10)
    cleanup = await restarted.claim_operation(entry["sandbox_id"], kind="recovery", lease_seconds=10)
    with pytest.raises(PreviewLeaseLostError):
        await first.save(entry["sandbox_id"], {"status": "running"}, token)
    with pytest.raises(PreviewLeaseLostError):
        await first.renew_operation(entry["sandbox_id"], token, 10)
    assert not await first.release_operation(entry["sandbox_id"], token)
    snapshot = await restarted.get(entry["sandbox_id"])
    assert snapshot["status"] == "error" and snapshot["preview_url"] is None
    assert await restarted.release(entry["sandbox_id"], operation_token=cleanup, provider_absent=True)
    assert await first.list() == []


async def test_renewal_keeps_operation_exclusive_and_release_is_fenced(storage):
    store = _store(storage)
    entry = await _allocate(store, await _reserve(store), storage[1][0])
    await _attach(store, entry)
    token = await store.claim_operation(entry["sandbox_id"], kind="start", lease_seconds=10)
    storage[1][0] += timedelta(seconds=8)
    await store.renew_operation(entry["sandbox_id"], token, 10)
    storage[1][0] += timedelta(seconds=5)
    with pytest.raises(PreviewOperationBusy):
        await _store(storage).claim_operation(entry["sandbox_id"], kind="stop", lease_seconds=10)
    with pytest.raises(PreviewLeaseLostError):
        await store.release(entry["sandbox_id"], operation_token=token, provider_absent=True)
    assert await store.release_operation(entry["sandbox_id"], token)
    cleanup = await store.claim_operation(entry["sandbox_id"], kind="stop", lease_seconds=10)
    assert await store.release(entry["sandbox_id"], operation_token=cleanup, provider_absent=True)


async def test_expired_mutation_lease_clears_visible_url_before_cleanup(storage):
    store = _store(storage)
    entry = await _allocate(store, await _reserve(store), storage[1][0])
    await _attach(store, entry)
    await store.claim_operation(entry["sandbox_id"], kind="sync", lease_seconds=10)
    before = await store.get(entry["sandbox_id"])
    assert before["status"] == "running" and before["preview_url"]
    storage[1][0] += timedelta(seconds=11)
    for snapshot in [await _store(storage).get(entry["sandbox_id"]), *(await _store(storage).list())]:
        assert snapshot["status"] == "error" and snapshot["preview_url"] is None
        assert "interrupted" in snapshot["last_error"]
        assert snapshot["revision"] == before["revision"]
    # The projection derives from the persisted lease; reads do not invent a
    # second mutation authority or discard the cleanup obligation.
    durable = await store._sessions_collection().find_one({"_id": entry["sandbox_id"]})
    assert durable["state"]["status"] == "running"
    assert durable["operation"] is not None


async def test_cleanup_can_retry_after_interrupted_capacity_release(storage, monkeypatch):
    from mozaiksai.core.sandbox.preview_sessions import ArtifactPreviewSessionManager
    from tests.test_artifact_preview_sessions import FakeSandboxAdapter

    storage[1][0] = datetime.now(UTC)
    store = _store(storage)
    adapter = FakeSandboxAdapter()
    manager = ArtifactPreviewSessionManager(provider_resolver=lambda: ("docker", adapter), store=store)
    state = await manager.create_or_reuse("cleanup-retry", app_id="host", user_id="owner", target_app_id="target", build_registry_id="build")
    original_change = store._change

    async def unavailable(_):
        raise RuntimeError("admission release unavailable")

    monkeypatch.setattr(store, "_change", unavailable)
    with pytest.raises(RuntimeError, match="release unavailable"):
        await manager.stop(state.sandbox_id)
    snapshot = await store.get(state.sandbox_id)
    assert snapshot["status"] == "error" and snapshot["preview_url"] is None
    assert snapshot["operation"] is None
    durable = await store._sessions_collection().find_one({"_id": state.sandbox_id})
    assert durable["closing"]
    with pytest.raises(PreviewRecoveryRequired):
        await store.claim_operation(state.sandbox_id, kind="start", lease_seconds=10)
    monkeypatch.setattr(store, "_change", original_change)
    await manager.stop(state.sandbox_id)
    assert await store.get(state.sandbox_id) is None


async def test_attachment_receipt_survives_coordinator_write_interruption(storage, monkeypatch):
    store = _store(storage)
    entry = await _allocate(store, await _reserve(store), storage[1][0])
    original_change = store._change

    async def unavailable(_):
        raise RuntimeError("coordination unavailable")

    monkeypatch.setattr(store, "_change", unavailable)
    with pytest.raises(RuntimeError, match="unavailable"):
        await _attach(store, entry)
    snapshot = await _store(storage).get(entry["sandbox_id"])
    assert snapshot["session_id"] == "provider-" + entry["sandbox_id"]
    assert snapshot["phase"] == "active"
    monkeypatch.setattr(store, "_change", original_change)
    assert (await _attach(store, entry))["phase"] == "active"


async def test_metadata_delete_failure_does_not_retain_capacity_or_revive_preview(storage, monkeypatch):
    from mozaiksai.core.sandbox.preview_sessions import ArtifactPreviewSessionManager
    from tests.test_artifact_preview_sessions import FakeSandboxAdapter

    storage[1][0] = datetime.now(UTC)
    store = _store(storage)
    adapter = FakeSandboxAdapter()
    manager = ArtifactPreviewSessionManager(provider_resolver=lambda: ("docker", adapter), store=store)
    manager._max_sessions = manager._max_owner_sessions = 1
    identity = dict(app_id="host", user_id="owner", target_app_id="target", build_registry_id="build")
    state = await manager.create_or_reuse("delete-outage", **identity)
    collection = store._sessions_collection()
    monkeypatch.setattr(store, "_sessions_collection", lambda: collection)
    original_delete = collection.delete_one

    async def unavailable(*args, **kwargs):
        raise RuntimeError("metadata deletion unavailable")

    monkeypatch.setattr(collection, "delete_one", unavailable)
    with pytest.raises(RuntimeError, match="metadata deletion unavailable"):
        await manager.stop(state.sandbox_id)
    assert ("terminate_session", {"session_id": state.session_id}) in adapter.calls
    assert await _store(storage).get(state.sandbox_id) is None
    assert await _store(storage).list() == []
    orphan = await collection.find_one({"_id": state.sandbox_id})
    assert orphan["closing"] and orphan["state"]["preview_url"] is None
    with pytest.raises(KeyError, match="not found"):
        await manager.require_owner(state.sandbox_id, app_id="host", user_id="owner")
    replacement = await manager.create_or_reuse("delete-outage", **identity)
    assert replacement.sandbox_id != state.sandbox_id
    assert sum(name == "create_session" for name, _ in adapter.calls) == 2
    monkeypatch.setattr(collection, "delete_one", original_delete)
    await manager.stop(replacement.sandbox_id)


async def test_payload_is_bounded_metadata_and_authority_errors_propagate(storage):
    store = _store(storage)
    entry = await _allocate(store, await _reserve(store), storage[1][0])
    await _attach(store, entry)
    token = await store.claim_operation(entry["sandbox_id"], kind="sync", lease_seconds=10)
    for payload in ({"last_files": {"secret": b"data"}}, {"manifest": b"binary"}, {"paths": [b"binary"]}, {"manifest": "x" * (1024 * 1024)}):
        with pytest.raises(ValueError):
            await store.save(entry["sandbox_id"], payload, token)
    with pytest.raises(PreviewLeaseLostError):
        await store.save(entry["sandbox_id"], {"status": "running"}, "unknown")


async def test_preview_lifespan_tolerates_missing_mongo_but_admission_fails_closed(monkeypatch):
    from mozaiksai.core import core_config
    from mozaiksai.core.sandbox import preview_sessions
    from tests.test_artifact_preview_sessions import FakeSandboxAdapter

    monkeypatch.delenv("MONGO_URI", raising=False)
    monkeypatch.delenv("MONGO_URI_SECRET_NAME", raising=False)

    def missing_mongo():
        raise RuntimeError("MONGO_URI is not configured")

    monkeypatch.setattr(core_config, "get_mongo_client", missing_mongo)
    adapter = FakeSandboxAdapter()
    manager = preview_sessions.ArtifactPreviewSessionManager(provider_resolver=lambda: ("docker", adapter))
    monkeypatch.setattr(preview_sessions, "get_artifact_preview_sessions", lambda: manager)
    async with preview_sessions.preview_sessions_lifespan(object()):
        # The optional maintenance loop may report the outage, while the host
        # remains usable and admission still requires durable authority.
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="MONGO_URI is not configured"):
            await manager.create_or_reuse("no-database", app_id="host", user_id="owner", target_app_id="target", build_registry_id="build")
    assert adapter.calls == []


async def test_multiple_managers_bound_allocation_peak_and_drain_queue(storage):
    from mozaiksai.core.ports.sandbox import SandboxSessionInfo
    from mozaiksai.core.sandbox.preview_sessions import ArtifactPreviewSessionManager
    from tests.test_artifact_preview_sessions import FakeSandboxAdapter

    storage[1][0] = datetime.now(UTC)

    class MeasuredProvider(FakeSandboxAdapter):
        def __init__(self):
            super().__init__()
            self.creating = 0
            self.peak_creating = 0
            self.live = {}
            self.peak_live = 0
            self.peak_owner = 0

        async def create_session(self, **kwargs):
            self.creating += 1
            self.peak_creating = max(self.peak_creating, self.creating)
            try:
                await asyncio.sleep(0.025)
                session_id = uuid4().hex
                self.live[session_id] = kwargs["metadata"]["user_id"]
                self.peak_live = max(self.peak_live, len(self.live))
                self.peak_owner = max(self.peak_owner, max(list(self.live.values()).count(owner) for owner in self.live.values()))
                return SandboxSessionInfo(session_id=session_id, provider="docker")
            finally:
                self.creating -= 1

        async def terminate_session(self, *, session_id):
            self.live.pop(session_id, None)
            return True

    adapter = MeasuredProvider()
    managers = [ArtifactPreviewSessionManager(provider_resolver=lambda: ("docker", adapter), store=_store(storage)) for _ in range(4)]
    for manager in managers:
        manager._max_sessions = 6
        manager._max_owner_sessions = 2
        manager._max_pending = 30
        manager._max_parallel_creates = 2
        manager._queue_seconds = 10
        manager._poll_seconds = 0.005

    async def client(index):
        manager = managers[index % len(managers)]
        started = time.monotonic()
        state = await manager.create_or_reuse(
            f"artifact-{index}", app_id="host", user_id=f"owner-{index % 4}",
            target_app_id="target", build_registry_id="build",
        )
        latency = time.monotonic() - started
        await asyncio.sleep(0.06)
        await managers[(index + 1) % len(managers)].stop(state.sandbox_id)
        return latency

    latencies = await asyncio.gather(*(client(index) for index in range(24)))
    assert adapter.peak_creating <= 2
    assert adapter.peak_live <= 6
    assert adapter.peak_owner <= 2
    assert max(latencies) > 0.05
    assert not adapter.live
    assert await managers[0]._store.list() == []
    print(json.dumps({"requests": len(latencies), "workers": len(managers), "peak_creating": adapter.peak_creating,
                      "peak_live": adapter.peak_live, "peak_owner": adapter.peak_owner, "maximum_queue_and_create_seconds": round(max(latencies), 3)}))


async def test_separate_process_recovers_owner_and_operates_existing_session(storage):
    if isinstance(storage[0], FakePreviewDatabase):
        pytest.skip("Cross-process proof requires real MongoDB")
    from mozaiksai.core.sandbox.preview_sessions import ArtifactPreviewSessionManager
    from tests.test_artifact_preview_sessions import FakeSandboxAdapter

    database, now = storage
    now[0] = datetime.now(UTC)
    store = _store(storage)
    manager = ArtifactPreviewSessionManager(provider_resolver=lambda: ("docker", FakeSandboxAdapter()), store=store)
    state = await manager.create_or_reuse("restart", app_id="host", user_id="owner", target_app_id="target", build_registry_id="build")
    await manager.sync(state.sandbox_id, [{"path": "app.json", "content": '{"appId":"target"}'}], [])
    await manager.start(state.sandbox_id)
    await manager.close()
    script = '''
import asyncio, json, os, sys
from motor.motor_asyncio import AsyncIOMotorClient
from mozaiksai.core.sandbox.preview_store import MongoPreviewStore
from mozaiksai.core.sandbox.preview_sessions import ArtifactPreviewSessionManager
from tests.test_artifact_preview_sessions import FakeSandboxAdapter
async def main():
    client = AsyncIOMotorClient(os.environ['MONGO_URI'])
    adapter = FakeSandboxAdapter()
    manager = ArtifactPreviewSessionManager(provider_resolver=lambda: ('docker', adapter), store=MongoPreviewStore(client[sys.argv[1]]))
    try:
        state = await manager.require_owner(sys.argv[2], app_id='host', user_id='owner')
        assert state.status == 'running' and state.session_id == sys.argv[3]
        try:
            await manager.require_owner(sys.argv[2], app_id='host', user_id='outsider')
        except KeyError:
            pass
        else:
            raise AssertionError('cross-owner access accepted')
        await manager.sync(state.sandbox_id, [{'path': 'brand/note.txt', 'content': 'new worker'}], [])
        updated = await manager.start(state.sandbox_id)
        assert updated.status == 'running'
        assert not any(name == 'create_session' for name, _ in adapter.calls)
        await manager.stop(state.sandbox_id)
        assert await manager._store.get(state.sandbox_id) is None
        print(json.dumps({'reused_provider_session': True, 'cross_owner_rejected': True, 'sync_start_stop': True}))
    finally:
        await manager.close()
        client.close()
asyncio.run(main())
'''
    environment = {**os.environ, "PYTHON_DOTENV_DISABLED": "1", "MONGO_URI": os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")}
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script, database.name, state.sandbox_id, state.session_id],
        env=environment, capture_output=True, text=True, timeout=30,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["sync_start_stop"]
    assert await store.get(state.sandbox_id) is None


@pytest.mark.parametrize("failure_persists", [False, True])
async def test_sync_receipt_failure_never_unlocks_unrecorded_remote_mutation(storage, monkeypatch, failure_persists):
    from mozaiksai.core.sandbox.preview_sessions import ArtifactPreviewSessionManager
    from tests.test_artifact_preview_sessions import FakeSandboxAdapter

    storage[1][0] = datetime.now(UTC)
    store = _store(storage)
    adapter = FakeSandboxAdapter()
    manager = ArtifactPreviewSessionManager(provider_resolver=lambda: ("docker", adapter), store=store)
    other_worker = ArtifactPreviewSessionManager(provider_resolver=lambda: ("docker", adapter), store=_store(storage))
    state = await manager.create_or_reuse("receipt-failure", app_id="host", user_id="owner", target_app_id="target", build_registry_id="build")
    await manager.sync(state.sandbox_id, [{"path": "app.json", "content": '{"appId":"target"}'}], [])
    await manager.start(state.sandbox_id)
    original_save = store.save

    async def failing_save(sandbox_id, payload, operation_token):
        if failure_persists or payload["status"] != "error":
            raise RuntimeError("metadata receipt unavailable")
        return await original_save(sandbox_id, payload, operation_token)

    monkeypatch.setattr(store, "save", failing_save)
    with pytest.raises(RuntimeError, match="receipt unavailable"):
        await manager.sync(state.sandbox_id, [{"path": "brand/note.txt", "content": "remote changed"}], [])
    writes = [kwargs for name, kwargs in adapter.calls if name == "write_files"]
    assert writes[-1]["files"] == {"app/brand/note.txt": "remote changed"}
    snapshot = await store.get(state.sandbox_id)
    if failure_persists:
        assert snapshot["operation"] is not None
        with pytest.raises(PreviewOperationBusy):
            await other_worker.start(state.sandbox_id)
        storage[1][0] += timedelta(seconds=manager._lease_seconds + 1)
        with pytest.raises(PreviewRecoveryRequired):
            await other_worker.start(state.sandbox_id)
    else:
        assert snapshot["status"] == "error" and snapshot["session_id"] is None
        assert snapshot["operation"] is None
        assert (await other_worker.start(state.sandbox_id)).status == "error"
    await other_worker.stop(state.sandbox_id)
    assert await store.get(state.sandbox_id) is None
