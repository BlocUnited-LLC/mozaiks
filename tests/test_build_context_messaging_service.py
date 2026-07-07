"""Service-level tests for the messaging build_context pack.

Strategy:
- Load service.py directly via importlib (no mozaiksai runtime needed).
- Inject fake repos via the service constructor.
- Use a simple FakeCtx with user_id and an async emit() stub.
- Cover every service method: happy paths, edge cases, permission checks.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_BACKEND = (
    Path(__file__).parent.parent
    / "factory_app"
    / "build_context"
    / "messaging"
    / "templates"
    / "modules"
    / "messages"
    / "backend"
)
_PACKAGE = "tests.messaging_template_backend"


def _ensure_backend_package() -> None:
    if _PACKAGE in sys.modules:
        return
    package = ModuleType(_PACKAGE)
    package.__path__ = [str(_BACKEND)]
    sys.modules[_PACKAGE] = package


def _load(name: str):
    _ensure_backend_package()
    path = _BACKEND / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{_PACKAGE}.{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PACKAGE}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


schemas = _load("schemas")


def _load_service():
    """Load service.py under a synthetic backend package so relative imports work."""
    _ensure_backend_package()
    _load("policy")
    _load("repo")

    svc_path = _BACKEND / "service.py"
    spec = importlib.util.spec_from_file_location(f"{_PACKAGE}.service", svc_path)
    svc_mod = importlib.util.module_from_spec(spec)

    # Inject a stub for mozaiksai to satisfy 'from mozaiksai...' in policy/repo
    mozaiks_stub = SimpleNamespace(
        core=SimpleNamespace(
            runtime=SimpleNamespace(persistence=SimpleNamespace(app_data_from_context=None))
        )
    )
    sys.modules.setdefault("mozaiksai", mozaiks_stub)
    sys.modules.setdefault("mozaiksai.core", mozaiks_stub.core)
    sys.modules.setdefault(
        "mozaiksai.core.runtime", mozaiks_stub.core.runtime
    )
    sys.modules.setdefault(
        "mozaiksai.core.runtime.persistence", mozaiks_stub.core.runtime.persistence
    )

    spec.loader.exec_module(svc_mod)
    return svc_mod


_svc_mod = _load_service()
MessageService = _svc_mod.MessageService


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ctx(user_id: str = "user_alice") -> SimpleNamespace:
    emitted = []

    async def emit(event_type, payload):
        emitted.append((event_type, payload))

    ctx = SimpleNamespace(user_id=user_id, emit=emit, _emitted=emitted)
    return ctx


def _thread(
    thread_id: str = "t1",
    creator: str = "user_alice",
    participants: list[str] | None = None,
    status: str = "open",
    thread_type: str = "dm",
    last_message_at: str | None = None,
    last_message: dict | None = None,
) -> dict:
    return {
        "thread_id": thread_id,
        "title": "",
        "thread_type": thread_type,
        "participant_ids": participants or ["user_alice", "user_bob"],
        "context_id": None,
        "status": status,
        "created_by": creator,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "last_message_at": last_message_at,
        "last_message": last_message,
    }


def _msg(
    message_id: str = "m1",
    thread_id: str = "t1",
    sender_id: str = "user_alice",
    body: str = "Hello",
    message_type: str = "text",
    is_deleted: bool = False,
    edited_at: str | None = None,
) -> dict:
    return {
        "message_id": message_id,
        "thread_id": thread_id,
        "sender_id": sender_id,
        "body": body,
        "message_type": message_type,
        "created_at": "2024-01-01T00:00:00+00:00",
        "edited_at": edited_at,
        "is_deleted": is_deleted,
    }


# ---------------------------------------------------------------------------
# Fake repos
# ---------------------------------------------------------------------------


class FakeThreadRepo:
    def __init__(self, threads: list[dict] | None = None):
        self._threads: list[dict] = list(threads or [])
        self.inserts: list[dict] = []

    async def list(self, ctx, *, query, limit, before=None):
        results = []
        for t in self._threads:
            match = all(self._matches_query_value(t.get(k), v) for k, v in query.items())
            if match:
                results.append(t)
        return results[:limit]

    @staticmethod
    def _matches_query_value(actual, expected) -> bool:
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            return True
        if isinstance(actual, list):
            return expected in actual
        return actual == expected

    async def get(self, ctx, *, thread_id):
        return next((t for t in self._threads if t["thread_id"] == thread_id), None)

    async def find_by_participants(self, ctx, *, participant_ids, thread_type="dm"):
        for t in self._threads:
            if (
                sorted(t["participant_ids"]) == sorted(participant_ids)
                and t["thread_type"] == thread_type
                and t["status"] == "open"
            ):
                return t
        return None

    async def insert(self, ctx, *, doc):
        self.inserts.append(dict(doc))
        self._threads.append(dict(doc))

    async def update_last_message(self, ctx, *, thread_id, now, preview):
        for t in self._threads:
            if t["thread_id"] == thread_id:
                t["last_message_at"] = now
                t["last_message"] = preview

    async def update_status(self, ctx, *, thread_id, status, now):
        for t in self._threads:
            if t["thread_id"] == thread_id:
                t["status"] = status
                return 1
        return 0

    async def remove_participant(self, ctx, *, thread_id, user_id, now):
        for t in self._threads:
            if t["thread_id"] == thread_id:
                t["participant_ids"] = [p for p in t["participant_ids"] if p != user_id]
                return 1
        return 0

    async def count_participants(self, ctx, *, thread_id):
        t = next((t for t in self._threads if t["thread_id"] == thread_id), None)
        return len(t["participant_ids"]) if t else 0


class FakeMessageRepo:
    def __init__(self, messages: list[dict] | None = None):
        self._messages: list[dict] = list(messages or [])
        self.inserts: list[dict] = []
        self.updates: list[tuple] = []

    async def list(self, ctx, *, thread_id, limit, before=None):
        msgs = [
            m for m in self._messages
            if m["thread_id"] == thread_id and not m.get("is_deleted")
        ]
        return msgs[-limit:]

    async def insert(self, ctx, *, doc):
        self.inserts.append(dict(doc))
        self._messages.append(dict(doc))

    async def get_by_id(self, ctx, *, thread_id, message_id):
        return next(
            (m for m in self._messages
             if m["thread_id"] == thread_id and m["message_id"] == message_id),
            None,
        )

    async def update(self, ctx, *, thread_id, message_id, updates):
        for m in self._messages:
            if m["thread_id"] == thread_id and m["message_id"] == message_id:
                m.update(updates)
                self.updates.append((message_id, dict(updates)))
                return 1
        return 0

    async def count_unread(self, ctx, *, thread_id, user_id, since):
        return len([
            m for m in self._messages
            if m["thread_id"] == thread_id
            and m["sender_id"] != user_id
            and m["created_at"] > since
            and not m.get("is_deleted")
        ])

    async def get_latest(self, ctx, *, thread_id):
        msgs = [m for m in self._messages if m["thread_id"] == thread_id and not m.get("is_deleted")]
        return msgs[-1] if msgs else None


class FakeReadStateRepo:
    def __init__(self, states: list[dict] | None = None):
        self._states: list[dict] = list(states or [])
        self.upserts: list[dict] = []
        self.deletes: list[tuple] = []

    async def get(self, ctx, *, thread_id, user_id):
        return next(
            (s for s in self._states if s["thread_id"] == thread_id and s["user_id"] == user_id),
            None,
        )

    async def list_for_user(self, ctx, *, user_id, thread_ids):
        return [s for s in self._states if s["user_id"] == user_id and s["thread_id"] in thread_ids]

    async def upsert(self, ctx, *, thread_id, user_id, message_id, now):
        self.upserts.append({"thread_id": thread_id, "user_id": user_id})
        for s in self._states:
            if s["thread_id"] == thread_id and s["user_id"] == user_id:
                s["read_at"] = now
                return
        self._states.append({"thread_id": thread_id, "user_id": user_id, "read_at": now, "last_read_message_id": message_id})

    async def delete_for_user(self, ctx, *, thread_id, user_id):
        self.deletes.append((thread_id, user_id))
        self._states = [s for s in self._states if not (s["thread_id"] == thread_id and s["user_id"] == user_id)]


class FakeNotificationRepo:
    def __init__(self, notifications: list[dict] | None = None):
        self._notifs: list[dict] = list(notifications or [])

    async def list(self, ctx, *, user_id, limit):
        return [n for n in self._notifs if n["user_id"] == user_id][:limit]

    async def count_unread(self, ctx, *, user_id):
        return len([n for n in self._notifs if n["user_id"] == user_id and not n.get("read")])

    async def insert(self, ctx, *, doc):
        self._notifs.append(dict(doc))

    async def mark_read(self, ctx, *, notification_id, user_id):
        for n in self._notifs:
            if n["notification_id"] == notification_id and n["user_id"] == user_id:
                n["read"] = True
                return 1
        return 0

    async def mark_all_read(self, ctx, *, user_id):
        for n in self._notifs:
            if n["user_id"] == user_id:
                n["read"] = True


def _service(
    threads: list[dict] | None = None,
    messages: list[dict] | None = None,
    states: list[dict] | None = None,
    notifications: list[dict] | None = None,
) -> MessageService:
    return MessageService(
        threads=FakeThreadRepo(threads),
        messages=FakeMessageRepo(messages),
        reads=FakeReadStateRepo(states),
        notifications=FakeNotificationRepo(notifications),
    )


# ---------------------------------------------------------------------------
# list_threads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_threads_returns_participant_threads():
    t = _thread(participants=["user_alice", "user_bob"])
    svc = _service(threads=[t])
    result = await svc.list_threads(_ctx("user_alice"))
    assert len(result["threads"]) == 1
    assert result["threads"][0]["thread_id"] == "t1"


@pytest.mark.asyncio
async def test_list_threads_cursor_field_present():
    t1 = _thread("t1", participants=["user_alice", "user_bob"])
    svc = _service(threads=[t1])
    # With a full page we get a next_cursor; with partial we don't
    result = await svc.list_threads(_ctx("user_alice"), limit=1)
    # next_cursor is set when len(threads) == limit
    assert "next_cursor" in result


# ---------------------------------------------------------------------------
# find_or_create_dm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_or_create_dm_creates_new_thread():
    svc = _service()
    ctx = _ctx("user_alice")
    result = await svc.find_or_create_dm(ctx, participant_id="user_bob")
    assert result["success"] is True
    assert result["created"] is True
    assert result["thread"]["thread_type"] == "dm"
    assert sorted(result["thread"]["participant_ids"]) == ["user_alice", "user_bob"]


@pytest.mark.asyncio
async def test_find_or_create_dm_returns_existing_thread():
    t = _thread(participants=["user_alice", "user_bob"], thread_type="dm")
    svc = _service(threads=[t])
    result = await svc.find_or_create_dm(_ctx("user_alice"), participant_id="user_bob")
    assert result["success"] is True
    assert result["created"] is False
    assert result["thread"]["thread_id"] == "t1"


@pytest.mark.asyncio
async def test_find_or_create_dm_rejects_self():
    svc = _service()
    result = await svc.find_or_create_dm(_ctx("user_alice"), participant_id="user_alice")
    assert result["success"] is False
    assert "yourself" in result["error"]


@pytest.mark.asyncio
async def test_find_or_create_dm_participant_order_canonical():
    """Canonical participant list is always sorted — same thread regardless of who initiates."""
    svc = _service()
    ctx_alice = _ctx("user_alice")
    ctx_bob = _ctx("user_bob")
    r1 = await svc.find_or_create_dm(ctx_alice, participant_id="user_bob")
    r2 = await svc.find_or_create_dm(ctx_bob, participant_id="user_alice")
    assert r1["thread"]["participant_ids"] == r2["thread"]["participant_ids"]


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_succeeds_for_participant():
    t = _thread(participants=["user_alice", "user_bob"])
    svc = _service(threads=[t])
    ctx = _ctx("user_alice")
    result = await svc.send_message(ctx, thread_id="t1", body="Hello Bob")
    assert result["success"] is True
    assert result["message"]["body"] == "Hello Bob"
    assert result["message"]["sender_id"] == "user_alice"
    assert result["message"]["edited_at"] is None
    assert result["message"]["is_deleted"] is False


@pytest.mark.asyncio
async def test_send_message_emits_event():
    t = _thread(participants=["user_alice", "user_bob"])
    svc = _service(threads=[t])
    ctx = _ctx("user_alice")
    await svc.send_message(ctx, thread_id="t1", body="Hey")
    assert any(et == "app.messages.message.sent" for et, _ in ctx._emitted)


@pytest.mark.asyncio
async def test_send_message_rejects_non_participant():
    t = _thread(participants=["user_alice", "user_bob"])
    svc = _service(threads=[t])
    result = await svc.send_message(_ctx("user_charlie"), thread_id="t1", body="Hi")
    assert result["success"] is False
    assert "access denied" in result["error"]


@pytest.mark.asyncio
async def test_send_message_rejects_closed_thread():
    t = _thread(participants=["user_alice", "user_bob"], status="closed")
    svc = _service(threads=[t])
    result = await svc.send_message(_ctx("user_alice"), thread_id="t1", body="Hi")
    assert result["success"] is False
    assert "closed" in result["error"]


@pytest.mark.asyncio
async def test_send_message_rejects_empty_body():
    t = _thread(participants=["user_alice", "user_bob"])
    svc = _service(threads=[t])
    result = await svc.send_message(_ctx("user_alice"), thread_id="t1", body="   ")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_send_message_rejects_too_long_body():
    t = _thread(participants=["user_alice", "user_bob"])
    svc = _service(threads=[t])
    body = "x" * (schemas.MAX_MESSAGE_LENGTH + 1)
    result = await svc.send_message(_ctx("user_alice"), thread_id="t1", body=body)
    assert result["success"] is False
    assert "character limit" in result["error"]


# ---------------------------------------------------------------------------
# edit_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_message_succeeds_for_sender():
    t = _thread(participants=["user_alice", "user_bob"])
    m = _msg(sender_id="user_alice")
    svc = _service(threads=[t], messages=[m])
    result = await svc.edit_message(_ctx("user_alice"), thread_id="t1", message_id="m1", body="Edited")
    assert result["success"] is True
    assert result["message"]["body"] == "Edited"
    assert result["message"]["edited_at"] is not None


@pytest.mark.asyncio
async def test_edit_message_emits_event():
    t = _thread(participants=["user_alice", "user_bob"])
    m = _msg(sender_id="user_alice")
    svc = _service(threads=[t], messages=[m])
    ctx = _ctx("user_alice")
    await svc.edit_message(ctx, thread_id="t1", message_id="m1", body="New")
    assert any(et == "app.messages.message.edited" for et, _ in ctx._emitted)


@pytest.mark.asyncio
async def test_edit_message_rejected_for_non_sender():
    t = _thread(participants=["user_alice", "user_bob"])
    m = _msg(sender_id="user_alice")
    svc = _service(threads=[t], messages=[m])
    result = await svc.edit_message(_ctx("user_bob"), thread_id="t1", message_id="m1", body="Edited")
    assert result["success"] is False
    assert "sender" in result["error"]


@pytest.mark.asyncio
async def test_edit_message_rejected_for_deleted_message():
    t = _thread(participants=["user_alice", "user_bob"])
    m = _msg(sender_id="user_alice", is_deleted=True)
    svc = _service(threads=[t], messages=[m])
    result = await svc.edit_message(_ctx("user_alice"), thread_id="t1", message_id="m1", body="Hi")
    assert result["success"] is False
    assert "deleted" in result["error"]


@pytest.mark.asyncio
async def test_edit_message_rejected_for_system_message():
    t = _thread(participants=["user_alice", "user_bob"])
    m = _msg(sender_id="user_alice", message_type="system")
    svc = _service(threads=[t], messages=[m])
    result = await svc.edit_message(_ctx("user_alice"), thread_id="t1", message_id="m1", body="Hi")
    assert result["success"] is False
    assert "system" in result["error"]


# ---------------------------------------------------------------------------
# delete_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_message_succeeds_for_sender():
    t = _thread(participants=["user_alice", "user_bob"])
    m = _msg(sender_id="user_alice")
    svc = _service(threads=[t], messages=[m])
    result = await svc.delete_message(_ctx("user_alice"), thread_id="t1", message_id="m1")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_delete_message_emits_event():
    t = _thread(participants=["user_alice", "user_bob"])
    m = _msg(sender_id="user_alice")
    svc = _service(threads=[t], messages=[m])
    ctx = _ctx("user_alice")
    await svc.delete_message(ctx, thread_id="t1", message_id="m1")
    assert any(et == "app.messages.message.deleted" for et, _ in ctx._emitted)


@pytest.mark.asyncio
async def test_delete_message_allowed_for_thread_creator():
    """Thread creator may delete any message, not just their own."""
    t = _thread(creator="user_alice", participants=["user_alice", "user_bob"])
    m = _msg(sender_id="user_bob")
    svc = _service(threads=[t], messages=[m])
    result = await svc.delete_message(_ctx("user_alice"), thread_id="t1", message_id="m1")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_delete_message_rejected_for_non_sender_non_creator():
    t = _thread(creator="user_alice", participants=["user_alice", "user_bob", "user_charlie"])
    m = _msg(sender_id="user_alice")
    svc = _service(threads=[t], messages=[m])
    result = await svc.delete_message(_ctx("user_charlie"), thread_id="t1", message_id="m1")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_delete_message_rejected_for_already_deleted():
    t = _thread(participants=["user_alice", "user_bob"])
    m = _msg(sender_id="user_alice", is_deleted=True)
    svc = _service(threads=[t], messages=[m])
    result = await svc.delete_message(_ctx("user_alice"), thread_id="t1", message_id="m1")
    assert result["success"] is False
    assert "already deleted" in result["error"]


# ---------------------------------------------------------------------------
# mark_thread_read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_thread_read_succeeds():
    t = _thread(participants=["user_alice", "user_bob"])
    m = _msg()
    svc = _service(threads=[t], messages=[m])
    result = await svc.mark_thread_read(_ctx("user_alice"), thread_id="t1")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_mark_thread_read_rejects_non_participant():
    t = _thread(participants=["user_alice", "user_bob"])
    svc = _service(threads=[t])
    result = await svc.mark_thread_read(_ctx("user_charlie"), thread_id="t1")
    assert result["success"] is False


# ---------------------------------------------------------------------------
# update_thread_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_thread_status_creator_can_close():
    t = _thread(creator="user_alice", participants=["user_alice", "user_bob"])
    svc = _service(threads=[t])
    result = await svc.update_thread_status(_ctx("user_alice"), thread_id="t1", status="closed")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_update_thread_status_non_creator_cannot_close():
    t = _thread(creator="user_alice", participants=["user_alice", "user_bob"])
    svc = _service(threads=[t])
    result = await svc.update_thread_status(_ctx("user_bob"), thread_id="t1", status="closed")
    assert result["success"] is False
    assert "creator" in result["error"]


@pytest.mark.asyncio
async def test_update_thread_status_any_participant_can_reopen():
    t = _thread(creator="user_alice", participants=["user_alice", "user_bob"], status="closed")
    svc = _service(threads=[t])
    result = await svc.update_thread_status(_ctx("user_bob"), thread_id="t1", status="open")
    assert result["success"] is True


# ---------------------------------------------------------------------------
# leave_thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_leave_thread_removes_participant():
    t = _thread(participants=["user_alice", "user_bob", "user_charlie"])
    svc = _service(threads=[t])
    ctx = _ctx("user_alice")
    result = await svc.leave_thread(ctx, thread_id="t1")
    assert result["success"] is True
    assert any(et == "app.messages.thread.left" for et, _ in ctx._emitted)


@pytest.mark.asyncio
async def test_leave_thread_auto_closes_when_empty():
    t = _thread(participants=["user_alice"])
    svc = _service(threads=[t])
    await svc.leave_thread(_ctx("user_alice"), thread_id="t1")
    assert t["status"] == "closed"


@pytest.mark.asyncio
async def test_leave_thread_rejects_non_participant():
    t = _thread(participants=["user_alice", "user_bob"])
    svc = _service(threads=[t])
    result = await svc.leave_thread(_ctx("user_charlie"), thread_id="t1")
    assert result["success"] is False


# ---------------------------------------------------------------------------
# get_unread_summary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_unread_summary_counts_threads_with_unread():
    now = "2024-06-01T00:00:00+00:00"
    later = "2024-06-01T01:00:00+00:00"
    t = _thread(
        participants=["user_alice", "user_bob"],
        last_message_at=later,
        last_message={"sender_id": "user_bob", "message_id": "m1", "body_preview": "Hi", "sent_at": later},
    )
    svc = _service(threads=[t], states=[
        {"thread_id": "t1", "user_id": "user_alice", "read_at": now, "last_read_message_id": None}
    ])
    result = await svc.get_unread_summary(_ctx("user_alice"))
    assert result["unread_thread_count"] == 1


@pytest.mark.asyncio
async def test_get_unread_summary_skips_own_messages():
    later = "2024-06-01T01:00:00+00:00"
    t = _thread(
        participants=["user_alice", "user_bob"],
        last_message_at=later,
        last_message={"sender_id": "user_alice", "message_id": "m1", "body_preview": "Hi", "sent_at": later},
    )
    svc = _service(threads=[t])
    result = await svc.get_unread_summary(_ctx("user_alice"))
    # last message was sent by alice herself — should NOT count as unread
    assert result["unread_thread_count"] == 0


# ---------------------------------------------------------------------------
# notifications
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_notifications_returns_user_notifications():
    notifs = [
        {"notification_id": "n1", "user_id": "user_alice", "read": False, "event_type": "x", "payload": {}, "created_at": "2024-01-01T00:00:00+00:00"},
        {"notification_id": "n2", "user_id": "user_bob", "read": False, "event_type": "x", "payload": {}, "created_at": "2024-01-01T00:00:00+00:00"},
    ]
    svc = _service(notifications=notifs)
    result = await svc.list_notifications(_ctx("user_alice"))
    assert result["unread_count"] == 1
    assert len(result["notifications"]) == 1


@pytest.mark.asyncio
async def test_mark_notification_read_updates_record():
    notifs = [
        {"notification_id": "n1", "user_id": "user_alice", "read": False, "event_type": "x", "payload": {}, "created_at": "2024-01-01T00:00:00+00:00"},
    ]
    svc = _service(notifications=notifs)
    result = await svc.mark_notification_read(_ctx("user_alice"), notification_id="n1")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_mark_notification_read_rejects_wrong_user():
    notifs = [
        {"notification_id": "n1", "user_id": "user_alice", "read": False, "event_type": "x", "payload": {}, "created_at": "2024-01-01T00:00:00+00:00"},
    ]
    svc = _service(notifications=notifs)
    result = await svc.mark_notification_read(_ctx("user_bob"), notification_id="n1")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_mark_all_notifications_read():
    notifs = [
        {"notification_id": "n1", "user_id": "user_alice", "read": False, "event_type": "x", "payload": {}, "created_at": "2024-01-01T00:00:00+00:00"},
        {"notification_id": "n2", "user_id": "user_alice", "read": False, "event_type": "x", "payload": {}, "created_at": "2024-01-01T00:00:00+00:00"},
    ]
    svc = _service(notifications=notifs)
    await svc.mark_all_notifications_read(_ctx("user_alice"))
    result = await svc.list_notifications(_ctx("user_alice"))
    assert result["unread_count"] == 0


# ---------------------------------------------------------------------------
# create_thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_thread_adds_creator_as_participant():
    svc = _service()
    ctx = _ctx("user_alice")
    result = await svc.create_thread(ctx, title="Team Chat", participant_ids=["user_bob"])
    assert result["success"] is True
    assert "user_alice" in result["thread"]["participant_ids"]
    assert "user_bob" in result["thread"]["participant_ids"]
    assert result["thread"]["thread_type"] == "group"


@pytest.mark.asyncio
async def test_create_thread_rejects_empty_title():
    svc = _service()
    result = await svc.create_thread(_ctx("user_alice"), title="  ")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_create_thread_deduplicates_participants():
    svc = _service()
    result = await svc.create_thread(
        _ctx("user_alice"), title="Chat", participant_ids=["user_alice", "user_bob"]
    )
    # user_alice should appear only once
    assert result["thread"]["participant_ids"].count("user_alice") == 1
