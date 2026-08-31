"""Real-Mongo cross-process proofs for durable workflow admission."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from mozaiksai.core.core_config import close_mongo_client
from mozaiksai.core.workflow.queue import (
    MongoWorkflowQueue,
    QueueAuthorityUnavailableError,
    QueueIdentityConflictError,
    QueueItem,
    QueueItemStatus,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _mongo_uri() -> str | None:
    return next(
        (value for name in ("MONGO_URI", "MONGODB_URI", "MONGO_URL") if (value := (os.getenv(name) or "").strip())),
        None,
    )


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


pytestmark = pytest.mark.skipif(not _mongo_reachable(), reason="requires reachable mongo:7")


@pytest.fixture(autouse=True)
def _fresh_client(monkeypatch):
    monkeypatch.setenv("MONGO_URI", _mongo_uri() or "")
    close_mongo_client()
    yield
    close_mongo_client()


def _item(tag: str) -> QueueItem:
    return QueueItem(
        item_id=f"admission-{tag}",
        tenant_id=f"tenant-{tag}",
        workspace_id=f"workspace-{tag}",
        app_id=f"app-{tag}",
        chat_id=f"chat-{tag}",
        workflow_name="GenesisBuild",
        run_id=f"chat-{tag}",
        operation_id=f"request-{tag}",
        request_digest="a" * 64,
        user_id="user-a",
    )


async def _cleanup(item_id: str) -> None:
    col = MongoWorkflowQueue()._required_col()
    await col.delete_many({"_id": item_id})


async def test_many_identical_producers_create_one_admission() -> None:
    tag = uuid4().hex
    item = _item(tag)
    queue = MongoWorkflowQueue()
    try:
        await queue.ensure_indexes()
        ids = await asyncio.gather(*(queue.enqueue(_item(tag)) for _ in range(32)))
        assert set(ids) == {item.item_id}
        assert await queue._required_col().count_documents({"_id": item.item_id}) == 1
    finally:
        await _cleanup(item.item_id)


async def test_same_operation_with_changed_request_is_rejected() -> None:
    tag = uuid4().hex
    item = _item(tag)
    queue = MongoWorkflowQueue()
    try:
        await queue.ensure_indexes()
        await queue.enqueue(item)
        changed = _item(tag)
        changed.request_digest = "b" * 64
        with pytest.raises(QueueIdentityConflictError, match="request_digest"):
            await queue.enqueue(changed)
    finally:
        await _cleanup(item.item_id)


_SUBPROCESS_CLAIMER = r"""
import asyncio, sys
from mozaiksai.core.workflow.queue import MongoWorkflowQueue
async def main():
    result = await MongoWorkflowQueue().claim_item(sys.argv[1], sys.argv[2], lease_seconds=60)
    print("CLAIMED" if result.claimed else "BUSY", flush=True)
asyncio.run(main())
"""


async def test_multiple_process_consumers_produce_one_owner() -> None:
    tag = uuid4().hex
    item = _item(tag)
    queue = MongoWorkflowQueue()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        await queue.ensure_indexes()
        await queue.enqueue(item)
        procs = [
            await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _SUBPROCESS_CLAIMER,
                item.item_id,
                f"worker-{index}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(_REPO_ROOT),
                env=env,
            )
            for index in range(6)
        ]
        outputs = []
        for proc in procs:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            assert proc.returncode == 0, stderr.decode()[:2000]
            outputs.append(stdout.decode().strip())
        assert outputs.count("CLAIMED") == 1, outputs
        assert outputs.count("BUSY") == 5, outputs
    finally:
        await _cleanup(item.item_id)


async def test_expired_prestart_claim_has_safe_successor_and_stale_release_fails() -> None:
    tag = uuid4().hex
    item = _item(tag)
    now = datetime.now(UTC)
    clock = [now]
    queue = MongoWorkflowQueue(_now_fn=lambda: clock[0])
    try:
        await queue.ensure_indexes()
        await queue.enqueue(item)
        first = await queue.claim_item(item.item_id, "old", lease_seconds=10)
        assert first.claimed and first.claim_token
        clock[0] = now + timedelta(seconds=11)
        successor = await queue.claim_item(item.item_id, "new", lease_seconds=60)
        assert successor.claimed and successor.claim_token != first.claim_token
        assert not await queue.complete(item.item_id, claim_token=first.claim_token)
        assert await queue.complete(item.item_id, claim_token=successor.claim_token)
    finally:
        await _cleanup(item.item_id)


async def test_expired_started_claim_dead_letters_without_reexecution() -> None:
    tag = uuid4().hex
    item = _item(tag)
    now = datetime.now(UTC)
    clock = [now]
    queue = MongoWorkflowQueue(_now_fn=lambda: clock[0])
    try:
        await queue.ensure_indexes()
        await queue.enqueue(item)
        claim = await queue.claim_item(item.item_id, "old", lease_seconds=10)
        assert claim.claimed and claim.claim_token
        assert await queue.mark_execution_started(item.item_id, claim_token=claim.claim_token)
        clock[0] = now + timedelta(seconds=11)
        assert not (await queue.claim_item(item.item_id, "new", lease_seconds=60)).claimed
        assert await queue.dead_letter_expired_started(item.item_id)
        stored = await queue.get(item.item_id)
        assert stored is not None
        assert stored.status is QueueItemStatus.DEAD_LETTER
        assert stored.error_category == "expired_after_execution_started"
    finally:
        await _cleanup(item.item_id)


async def test_expired_prestart_claims_exhaust_bounded_budget() -> None:
    tag = uuid4().hex
    item = _item(tag)
    now = datetime.now(UTC)
    clock = [now]
    queue = MongoWorkflowQueue(_now_fn=lambda: clock[0])
    try:
        await queue.ensure_indexes()
        await queue.enqueue(item, max_attempts=2)
        assert (await queue.claim_item(item.item_id, "one", lease_seconds=10)).claimed
        clock[0] = now + timedelta(seconds=11)
        assert (await queue.claim_item(item.item_id, "two", lease_seconds=10)).claimed
        clock[0] = now + timedelta(seconds=22)
        assert not (await queue.claim_item(item.item_id, "three", lease_seconds=10)).claimed
        assert await queue.dead_letter_exhausted(item.item_id)
        stored = await queue.get(item.item_id)
        assert stored is not None
        assert stored.status is QueueItemStatus.DEAD_LETTER
        assert stored.attempt_count == 2
        assert stored.error_category == "claim_attempts_exhausted"
    finally:
        await _cleanup(item.item_id)


async def test_queue_authority_outage_is_not_reported_as_contention(monkeypatch) -> None:
    queue = MongoWorkflowQueue()
    monkeypatch.setattr(queue, "_col", lambda *_args, **_kwargs: None)
    with pytest.raises(QueueAuthorityUnavailableError):
        await queue.enqueue(_item(uuid4().hex))
    with pytest.raises(QueueAuthorityUnavailableError):
        await queue.claim_next("worker-a")


async def test_generic_consumer_claims_respect_bounded_budget() -> None:
    tag = uuid4().hex
    item = _item(tag)
    now = datetime.now(UTC)
    clock = [now]
    queue = MongoWorkflowQueue(_now_fn=lambda: clock[0])
    try:
        await queue.ensure_indexes()
        await queue.enqueue(item, max_attempts=1)
        first = await queue.claim_next("one", lease_seconds=10)
        assert first.claimed
        clock[0] = now + timedelta(seconds=11)
        assert not (await queue.claim_next("two", lease_seconds=10)).claimed
        assert await queue.dead_letter_exhausted(item.item_id)
    finally:
        await _cleanup(item.item_id)
