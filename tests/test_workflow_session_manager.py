from __future__ import annotations

from types import SimpleNamespace

import pytest

from mozaiksai.core.workflow import session_manager


class _MemoryCollection:
    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}

    async def replace_one(self, filter_query, doc, upsert=False):  # noqa: ANN001
        _ = upsert
        self._docs[str(filter_query["_id"])] = dict(doc)

    async def update_one(self, filter_query, update):  # noqa: ANN001
        for doc_id, doc in self._docs.items():
            if all(doc.get(key) == value for key, value in filter_query.items()):
                updated = dict(doc)
                for key, value in (update.get("$set") or {}).items():
                    if "." not in key:
                        updated[key] = value
                        continue
                    current = updated
                    parts = key.split(".")
                    for part in parts[:-1]:
                        current = current.setdefault(part, {})
                    current[parts[-1]] = value
                self._docs[doc_id] = updated
                return

    async def find_one(self, query):  # noqa: ANN001
        for doc in self._docs.values():
            if all(doc.get(key) == value for key, value in query.items()):
                return dict(doc)
        return None


class _MemoryDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, _MemoryCollection] = {}

    def __getitem__(self, name: str) -> _MemoryCollection:
        if name not in self._collections:
            self._collections[name] = _MemoryCollection()
        return self._collections[name]


class _MemoryClient:
    def __init__(self) -> None:
        self._databases: dict[str, _MemoryDatabase] = {}

    def __getitem__(self, name: str) -> _MemoryDatabase:
        if name not in self._databases:
            self._databases[name] = _MemoryDatabase()
        return self._databases[name]


@pytest.mark.asyncio
async def test_workflow_session_manager_uses_named_runtime_collections(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _MemoryClient()

    async def _ensure_client() -> None:
        return None

    class _FakeAG2PersistenceManager:
        def __init__(self) -> None:
            self.persistence = SimpleNamespace(client=client, _ensure_client=_ensure_client)

    monkeypatch.setattr(session_manager, "AG2PersistenceManager", _FakeAG2PersistenceManager)

    session = await session_manager.create_workflow_session(
        app_id="app-1",
        user_id="user-1",
        workflow_name="ValueEngine",
    )
    artifact = await session_manager.create_artifact_instance(
        app_id="app-1",
        workflow_name="ValueEngine",
        artifact_type="AppIntelligenceOverviewCard",
        initial_state={"selected": "Add AI Workflows"},
    )
    await session_manager.attach_artifact_to_session(session["_id"], artifact["_id"], "app-1")
    await session_manager.update_artifact_state(artifact["_id"], "app-1", {"selected": "Build App Features"})

    loaded_session = await session_manager.get_workflow_session(session["_id"], "app-1")
    loaded_artifact = await session_manager.get_artifact_instance(artifact["_id"], "app-1")

    assert loaded_session is not None
    assert loaded_session["artifact_instance_id"] == artifact["_id"]
    assert loaded_artifact is not None
    assert loaded_artifact["last_active_chat_id"] == session["_id"]
    assert loaded_artifact["state"]["selected"] == "Build App Features"
