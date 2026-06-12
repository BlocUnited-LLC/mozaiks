from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import pytest

admin_router = importlib.import_module("mozaiksai.core.admin.router")


class _FakeAsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._iter = iter(())

    def sort(self, field: str, direction: int):
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        self._docs.sort(key=lambda doc: doc.get(field) or epoch, reverse=direction < 0)
        return self

    def limit(self, count: int):
        self._docs = self._docs[:count]
        return self

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeChatSessionsCollection:
    def __init__(self, *, aggregate_docs=None, find_docs=None):
        self.aggregate_docs = list(aggregate_docs or [])
        self.find_docs = list(find_docs or [])
        self.aggregate_calls = []
        self.find_calls = []

    def aggregate(self, pipeline):
        self.aggregate_calls.append(pipeline)
        return _FakeAsyncCursor(self.aggregate_docs)

    def find(self, query, projection):
        self.find_calls.append((dict(query), dict(projection)))
        docs = []
        for doc in self.find_docs:
            include = True
            for key, value in query.items():
                if doc.get(key) != value:
                    include = False
                    break
            if include:
                docs.append(doc)
        return _FakeAsyncCursor(docs)


@pytest.mark.asyncio
async def test_build_persisted_admin_stats_reads_chat_session_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _FakeChatSessionsCollection(
        aggregate_docs=[
            {
                "tracked_chats": 3,
                "active_chats": 1,
                "completed_chats": 2,
            }
        ]
    )
    monkeypatch.setattr(admin_router, "_get_chat_sessions_collection", lambda: collection)

    stats = await admin_router._build_persisted_admin_stats()

    assert stats == {
        "active_chats": 1,
        "tracked_chats": 3,
        "completed_chats": 2,
        "telemetry_source": "ag2_opentelemetry",
    }
    assert len(collection.aggregate_calls) == 1
    project_stage = collection.aggregate_calls[0][0]["$project"]
    assert "usage_" + "prompt_tokens_final" not in project_stage
    assert "tool_calls" + "_final" not in project_stage


@pytest.mark.asyncio
async def test_build_persisted_admin_runs_maps_sessions_to_existing_ui_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    collection = _FakeChatSessionsCollection(
        find_docs=[
            {
                "_id": "chat-running",
                "app_id": "app-1",
                "workflow_name": "ValueEngine",
                "user_id": "user-1",
                "status": 0,
                "created_at": now - timedelta(seconds=90),
                "completed_at": None,
                "duration_sec": 0.0,
                "messages": [
                    {"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Hello"},
                ],
            },
            {
                "_id": "chat-complete",
                "app_id": "app-1",
                "workflow_name": "PlanBuilder",
                "user_id": "user-2",
                "status": 1,
                "created_at": now - timedelta(minutes=10),
                "completed_at": now - timedelta(minutes=9, seconds=40),
                "duration_sec": 15.0,
                "messages": [
                    {"role": "assistant", "content": "Draft 1"},
                    {"role": "assistant", "content": "Draft 2"},
                ],
            },
            {
                "_id": "chat-other-app",
                "app_id": "app-2",
                "workflow_name": "OtherWorkflow",
                "user_id": "user-3",
                "status": 1,
                "created_at": now - timedelta(minutes=20),
                "completed_at": now - timedelta(minutes=19, seconds=50),
                "duration_sec": 10.0,
                "messages": [],
            },
        ]
    )
    monkeypatch.setattr(admin_router, "_get_chat_sessions_collection", lambda: collection)
    monkeypatch.setattr(admin_router, "_utc_now", lambda: now)

    response = await admin_router._build_persisted_admin_runs(app_id="app-1", active_only=False, limit=10)

    assert response["total"] == 2
    assert [run["chat_id"] for run in response["runs"]] == ["chat-running", "chat-complete"]
    assert response["runs"][0]["ended_at"] is None
    assert response["runs"][0]["runtime_sec"] == 90.0
    assert response["runs"][0]["agent_turns"] == 1
    assert response["runs"][0]["telemetry_source"] == "ag2_opentelemetry"
    assert "tool_calls" not in response["runs"][0]
    assert "prompt_tokens" not in response["runs"][0]
    assert response["runs"][1]["ended_at"] == (now - timedelta(minutes=9, seconds=40)).isoformat()
    assert response["runs"][1]["runtime_sec"] == 20.0
    assert response["runs"][1]["agent_turns"] == 2


@pytest.mark.asyncio
async def test_build_persisted_admin_runs_respects_active_only_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    collection = _FakeChatSessionsCollection(
        find_docs=[
            {
                "_id": "chat-1",
                "app_id": "app-1",
                "workflow_name": "A",
                "user_id": "u1",
                "status": 0,
                "created_at": now - timedelta(minutes=1),
                "completed_at": None,
                "duration_sec": 0.0,
                "messages": [],
            },
            {
                "_id": "chat-2",
                "app_id": "app-1",
                "workflow_name": "B",
                "user_id": "u2",
                "status": 0,
                "created_at": now - timedelta(minutes=2),
                "completed_at": None,
                "duration_sec": 0.0,
                "messages": [],
            },
            {
                "_id": "chat-3",
                "app_id": "app-1",
                "workflow_name": "C",
                "user_id": "u3",
                "status": 1,
                "created_at": now - timedelta(minutes=3),
                "completed_at": now - timedelta(minutes=2, seconds=30),
                "duration_sec": 30.0,
                "messages": [],
            },
        ]
    )
    monkeypatch.setattr(admin_router, "_get_chat_sessions_collection", lambda: collection)
    monkeypatch.setattr(admin_router, "_utc_now", lambda: now)

    response = await admin_router._build_persisted_admin_runs(app_id="app-1", active_only=True, limit=1)

    assert response["total"] == 1
    assert [run["chat_id"] for run in response["runs"]] == ["chat-1"]


@pytest.mark.asyncio
async def test_admin_usage_endpoint_delegates_to_runtime_usage_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeLedger:
        async def query_usage(self, *, app_id=None, user_id=None, limit=200):
            return {
                "app_id": app_id,
                "user_id": user_id,
                "limit": limit,
                "totals": {"total_tokens": 123},
            }

    monkeypatch.setattr(
        "mozaiksai.core.usage.get_runtime_usage_ledger",
        lambda: _FakeLedger(),
    )

    response = await admin_router.get_admin_usage(
        app_id="app-1",
        user_id="user-1",
        limit=50,
        user=object(),
    )

    assert response == {
        "app_id": "app-1",
        "user_id": "user-1",
        "limit": 50,
        "totals": {"total_tokens": 123},
    }
