"""Real-Mongo replay and acknowledgement boundaries for Factory callbacks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from factory_app.workflows._shared.platform import build_lifecycle as hooks
from factory_app.workflows.AppGenerator.tools.platform import build_events_outbox as outbox


@pytest_asyncio.fixture
async def collection(monkeypatch):
    name = f"build_event_replay_{uuid4().hex}"
    client = AsyncIOMotorClient(
        os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=1500,
    )
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real Mongo is required for build-event replay acceptance")
        pytest.skip("Mongo is unavailable")
    coll = client[name].BuildEventsOutbox
    monkeypatch.setattr(outbox, "_coll", AsyncMock(return_value=coll))
    monkeypatch.setattr(outbox, "_INDEX_READY", False)
    monkeypatch.setattr(outbox, "_INDEX_LOCK", asyncio.Lock())
    monkeypatch.setattr(hooks, "_spawn_delivery", lambda **kwargs: None)
    try:
        yield coll
    finally:
        assert name.startswith("build_event_replay_")
        await client.drop_database(name)
        client.close()


async def _emit(*, status="started", timestamp="2026-09-12T12:00:00Z", host="factory", execution="exec-one"):
    context = {"app_id": host, "build_id": "build-one", "user_id": "owner"}
    return await hooks._emit_event(
        event_type=f"build.{status}", status=status, context=context, workflow_name="AppGenerator",
        payload={
            "appId": host, "targetAppId": "target-app", "buildId": "build-one",
            "buildRegistryId": "appreg-one", "phase": "genesis", "userId": "owner",
            "chatId": "chat-one", "executionId": execution, "workflowName": "AppGenerator",
            "timestamp": timestamp,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["started", "completed", "failed"])
async def test_reemission_preserves_original_payload_and_retry_schedule(collection, status):
    event_id = await _emit(status=status)
    await outbox.mark_attempt(outbox_id=event_id, ok=False, error="acknowledgement lost")
    original = await collection.find_one({"_id": event_id})
    assert original["attempts"] == 1

    assert await _emit(status=status, timestamp="2026-09-12T12:05:00Z", execution="exec-replay") == event_id
    assert await collection.find_one({"_id": event_id}) == original


@pytest.mark.asyncio
async def test_concurrent_enqueues_store_one_complete_original_event(collection):
    ids = await asyncio.gather(*(_emit(execution=f"exec-{index}") for index in range(12)))
    assert len(set(ids)) == 1
    assert await collection.count_documents({}) == 1
    original = await collection.find_one({"_id": ids[0]})
    assert original["payload"]["executionId"] in {f"exec-{index}" for index in range(12)}
    assert original["attempts"] == 0
    await _emit(execution="late-replay")
    assert await collection.find_one({"_id": ids[0]}) == original


@pytest.mark.asyncio
async def test_same_build_and_event_in_different_hosts_remain_separate(collection):
    first = await _emit(host="factory-one")
    second = await _emit(host="factory-two")
    assert first != second
    assert await collection.count_documents({}) == 2
    for event_id, host in [(first, "factory-one"), (second, "factory-two")]:
        stored = await collection.find_one({"_id": event_id})
        assert stored["app_id"] == stored["payload"]["appId"] == host


@pytest.mark.asyncio
async def test_late_failed_attempt_cannot_revoke_success(collection):
    event_id = await _emit()
    await outbox.mark_attempt(outbox_id=event_id, ok=True, status_code=202)
    acknowledged = await collection.find_one({"_id": event_id})
    await outbox.mark_attempt(outbox_id=event_id, ok=False, status_code=503, error="late failure")
    assert await collection.find_one({"_id": event_id}) == acknowledged
    assert await outbox.list_due_events() == []


@pytest.mark.asyncio
async def test_success_between_failure_read_and_write_still_wins(collection, monkeypatch):
    event_id = await _emit()
    acknowledged = None

    async def find_then_ack(*args, **kwargs):
        nonlocal acknowledged
        doc = await collection.find_one(*args, **kwargs)
        await collection.update_one({"_id": event_id}, {"$set": {
            "platform_notified": True, "next_retry_at": None, "last_status_code": 202,
        }})
        acknowledged = await collection.find_one({"_id": event_id})
        return doc

    proxy = SimpleNamespace(find_one=find_then_ack, update_one=collection.update_one)
    monkeypatch.setattr(outbox, "_coll", AsyncMock(return_value=proxy))
    await outbox.mark_attempt(outbox_id=event_id, ok=False, error="late transport timeout")
    assert await collection.find_one({"_id": event_id}) == acknowledged


@pytest.mark.asyncio
async def test_lost_ack_reemission_delivers_identical_payload(collection, monkeypatch):
    delivered = []
    receipts = {}

    async def receive(*, app_id, payload):
        delivered.append(deepcopy(payload))
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        key = (app_id, payload["idempotencyKey"])
        previous = receipts.setdefault(key, digest)
        if previous != digest:
            return SimpleNamespace(ok=False, status_code=409, error="conflicting replay")
        if len(delivered) == 1:
            return SimpleNamespace(ok=False, status_code=None, error="acknowledgement lost")
        return SimpleNamespace(ok=True, status_code=202, error=None)

    client = SimpleNamespace(post_build_event=receive)
    monkeypatch.setattr(hooks, "_build_events_client", lambda: client)
    event_id = await _emit()
    await hooks._deliver_outbox_event_once(outbox_event_id=event_id)
    assert (await collection.find_one({"_id": event_id}))["platform_notified"] is False
    assert await _emit(timestamp="2026-09-12T12:05:00Z", execution="exec-replay") == event_id
    await hooks._deliver_outbox_event_once(outbox_event_id=event_id)
    assert len(delivered) == 2
    assert delivered[0] == delivered[1]
    assert (await collection.find_one({"_id": event_id}))["platform_notified"] is True
    assert await outbox.list_due_events() == []


@pytest.mark.asyncio
async def test_acknowledged_event_is_not_posted_again(collection, monkeypatch):
    event_id = await _emit()
    await outbox.mark_attempt(outbox_id=event_id, ok=True, status_code=202)
    client = AsyncMock()
    client.post_build_event.return_value = SimpleNamespace(ok=True, status_code=202, error=None)
    monkeypatch.setattr(hooks, "_build_events_client", lambda: client)
    await hooks._deliver_outbox_event_once(outbox_event_id=event_id)
    client.post_build_event.assert_not_awaited()
