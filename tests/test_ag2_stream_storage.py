from __future__ import annotations

from types import SimpleNamespace

import pytest
from autogen.beta import MemoryStream
from autogen.beta.events import HumanInputRequest, ToolCallsEvent

from mozaiksai.core.adapters.ag2_stream_storage import MongoAG2StreamStorage, stream_id_for_run


class _FakeCursor:
    def __init__(self, docs):  # noqa: ANN001
        self._docs = list(docs)

    def sort(self, key, direction):  # noqa: ANN001
        reverse = int(direction or 1) < 0
        self._docs.sort(key=lambda item: item.get(key, 0), reverse=reverse)
        return self

    def limit(self, n):  # noqa: ANN001
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):  # noqa: ANN001
        if length is None:
            return list(self._docs)
        return list(self._docs)[:length]


class _FakeEventsCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def create_index(self, keys, **kwargs):  # noqa: ANN001
        return None

    async def insert_one(self, doc):  # noqa: ANN001
        self.docs.append(dict(doc))

    async def insert_many(self, docs):  # noqa: ANN001
        for doc in docs:
            self.docs.append(dict(doc))

    def find(self, query, projection=None):  # noqa: ANN001
        matched = []
        for doc in self.docs:
            include = True
            for key, value in query.items():
                if doc.get(key) != value:
                    include = False
                    break
            if include:
                matched.append(dict(doc))
        return _FakeCursor(matched)

    async def delete_many(self, query):  # noqa: ANN001
        kept = []
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                continue
            kept.append(doc)
        self.docs = kept


class _FakeHeadsCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.find_one_and_update_calls: list[tuple] = []

    @staticmethod
    def _assert_no_operator_conflicts(update):  # noqa: ANN001
        update = update or {}
        operator_keys = {
            name: set((update.get(name) or {}).keys())
            for name in ("$inc", "$set", "$setOnInsert")
        }
        conflicts: set[str] = set()
        names = list(operator_keys)
        for index, left_name in enumerate(names):
            for right_name in names[index + 1:]:
                conflicts.update(operator_keys[left_name] & operator_keys[right_name])
        if conflicts:
            raise AssertionError(f"conflicting Mongo update operators for keys: {sorted(conflicts)}")

    async def create_index(self, keys, **kwargs):  # noqa: ANN001
        return None

    async def find_one_and_update(self, query, update, upsert=False, return_document=None):  # noqa: ANN001
        self._assert_no_operator_conflicts(update)
        self.find_one_and_update_calls.append((dict(query), update, upsert, return_document))
        stream_id = str(query.get("stream_id"))
        current = dict(self.docs.get(stream_id) or {})
        if not current and upsert:
            current.update((update or {}).get("$setOnInsert") or {})
            current.update({"stream_id": stream_id})
        for key, value in ((update or {}).get("$set") or {}).items():
            current[key] = value
        for key, value in ((update or {}).get("$inc") or {}).items():
            current[key] = int(current.get(key, 0) or 0) + int(value)
        self.docs[stream_id] = current
        return dict(current)

    async def update_one(self, query, update, upsert=False):  # noqa: ANN001
        self._assert_no_operator_conflicts(update)
        stream_id = str(query.get("stream_id"))
        current = dict(self.docs.get(stream_id) or {})
        if not current and upsert:
            current.update((update or {}).get("$setOnInsert") or {})
            current.update({"stream_id": stream_id})
        for key, value in ((update or {}).get("$set") or {}).items():
            current[key] = value
        self.docs[stream_id] = current

    async def delete_one(self, query):  # noqa: ANN001
        self.docs.pop(str(query.get("stream_id")), None)


@pytest.mark.asyncio
async def test_ag2_stream_storage_round_trips_history_through_memory_stream() -> None:
    events = _FakeEventsCollection()
    heads = _FakeHeadsCollection()
    storage = MongoAG2StreamStorage(app_id="app_1", events_collection=events, heads_collection=heads)
    stream = MemoryStream(storage=storage, id=stream_id_for_run("app_1", "chat_1"))

    original_events = [HumanInputRequest("Need more input"), ToolCallsEvent()]
    await storage.set_history(stream.id, original_events)

    restored_events = list(await stream.history.get_events())

    assert [type(event).__name__ for event in restored_events] == ["HumanInputRequest", "ToolCallsEvent"]
    assert restored_events[0].content == "Need more input"


@pytest.mark.asyncio
async def test_ag2_stream_storage_save_event_appends_sequences() -> None:
    events = _FakeEventsCollection()
    heads = _FakeHeadsCollection()
    storage = MongoAG2StreamStorage(app_id="app_1", events_collection=events, heads_collection=heads)
    stream = MemoryStream(storage=storage, id=stream_id_for_run("app_1", "chat_2"))
    context = SimpleNamespace(stream=stream)

    await storage.save_event(HumanInputRequest("one"), context)
    await storage.save_event(HumanInputRequest("two"), context)

    restored_events = list(await storage.get_history(stream.id))

    assert [event.content for event in restored_events] == ["one", "two"]
    assert [doc["sequence"] for doc in events.docs] == [1, 2]


@pytest.mark.asyncio
async def test_ag2_stream_storage_next_sequence_only_updates_counter_with_inc() -> None:
    events = _FakeEventsCollection()
    heads = _FakeHeadsCollection()
    storage = MongoAG2StreamStorage(app_id="app_1", events_collection=events, heads_collection=heads)
    stream = MemoryStream(storage=storage, id=stream_id_for_run("app_1", "chat_3"))
    context = SimpleNamespace(stream=stream)

    await storage.save_event(HumanInputRequest("one"), context)

    update = heads.find_one_and_update_calls[0][1]
    assert update["$inc"] == {"next_sequence": 1}
    assert "next_sequence" not in update["$set"]
    assert "next_sequence" not in update["$setOnInsert"]

