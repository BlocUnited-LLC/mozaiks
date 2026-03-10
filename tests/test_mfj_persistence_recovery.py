# ==============================================================================
# Tests for MFJCompletionStore — persistence and recovery logic
# (mozaiksai.core.workflow.pack.mfj_persistence)
# ==============================================================================

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.import_utils import import_module_directly

_persist_mod = import_module_directly("mozaiksai.core.workflow.pack.mfj_persistence")
MFJCompletionStore = _persist_mod.MFJCompletionStore


# ---------------------------------------------------------------------------
# Async cursor helper — Motor uses async iteration, NOT to_list in load methods
# ---------------------------------------------------------------------------


class _AsyncCursor:
    """Minimal async-iterable cursor for mocking Motor find() results."""

    def __init__(self, docs):
        self._docs = list(docs)
        self._idx = 0

    def sort(self, *a, **kw):
        return self

    def __aiter__(self):
        self._idx = 0
        return self

    async def __anext__(self):
        if self._idx >= len(self._docs):
            raise StopAsyncIteration
        doc = self._docs[self._idx]
        self._idx += 1
        return doc

    async def to_list(self, *a, **kw):
        return list(self._docs)


# ---------------------------------------------------------------------------
# Helpers — build fake Mongo collection
# ---------------------------------------------------------------------------


def _make_fake_collection(docs=None):
    """Return a MagicMock emulating a Motor collection."""
    docs = list(docs or [])
    coll = MagicMock()

    # list_indexes() — sync call → returns cursor with async .to_list()
    # Empty result means all three indexes will be created on first call.
    _idx_cursor = MagicMock()
    _idx_cursor.to_list = AsyncMock(return_value=[])
    coll.list_indexes = MagicMock(return_value=_idx_cursor)

    # find() — sync call → returns a fresh async-iterable cursor each time
    coll.find = MagicMock(side_effect=lambda *a, **kw: _AsyncCursor(docs))

    # update_one — async
    coll.update_one = AsyncMock(return_value=MagicMock(upserted_id=None))

    # create_index — async
    coll.create_index = AsyncMock()

    return coll


def _make_store(docs=None):
    """Return an MFJCompletionStore with a patched _coll()."""
    store = MFJCompletionStore.__new__(MFJCompletionStore)
    store._collection_name = "MFJCompletions"
    store._ttl_seconds = 604800
    store._indexes_ready = False
    fake_coll = _make_fake_collection(docs)
    store._coll = AsyncMock(return_value=fake_coll)
    store._fake_coll = fake_coll  # keep reference for assertions
    return store


# ---------------------------------------------------------------------------
# ensure_indexes
# ---------------------------------------------------------------------------


class TestEnsureIndexes:
    @pytest.mark.asyncio
    async def test_creates_indexes_on_first_call(self):
        store = _make_store()
        await store.ensure_indexes()
        assert store._fake_coll.create_index.called

    @pytest.mark.asyncio
    async def test_idempotent_does_not_recreate(self):
        store = _make_store()
        await store.ensure_indexes()
        call_count_after_first = store._fake_coll.create_index.call_count
        await store.ensure_indexes()
        # No new create_index calls after the guard flips to True
        assert store._fake_coll.create_index.call_count == call_count_after_first


# ---------------------------------------------------------------------------
# write_completion
# ---------------------------------------------------------------------------


class TestWriteCompletion:
    @pytest.mark.asyncio
    async def test_calls_update_one_with_upsert(self):
        store = _make_store()
        await store.write_completion(
            app_id="app1",
            parent_chat_id="chat_parent_1",
            trigger_id="trigger_a",
            mfj_cycle=1,
            child_count=3,
            succeeded_count=3,
            failed_count=0,
            child_chat_ids=["c1", "c2", "c3"],
        )
        store._fake_coll.update_one.assert_called_once()
        call_kwargs = store._fake_coll.update_one.call_args
        # upsert=True must be set
        assert call_kwargs.kwargs.get("upsert") is True or (
            len(call_kwargs.args) > 2 and call_kwargs.args[2].get("upsert") is True
        )

    @pytest.mark.asyncio
    async def test_filter_contains_all_three_keys(self):
        store = _make_store()
        await store.write_completion(
            app_id="app_x",
            parent_chat_id="chat_p",
            trigger_id="trig_1",
            mfj_cycle=2,
            child_count=2,
            succeeded_count=2,
            failed_count=0,
            child_chat_ids=["a", "b"],
        )
        filter_arg = store._fake_coll.update_one.call_args.args[0]
        assert filter_arg["app_id"] == "app_x"
        assert filter_arg["parent_chat_id"] == "chat_p"
        assert filter_arg["trigger_id"] == "trig_1"

    @pytest.mark.asyncio
    async def test_set_payload_contains_counts(self):
        store = _make_store()
        await store.write_completion(
            app_id="app1",
            parent_chat_id="chat1",
            trigger_id="t1",
            mfj_cycle=1,
            child_count=4,
            succeeded_count=3,
            failed_count=1,
            child_chat_ids=["c1", "c2", "c3", "c4"],
        )
        update_arg = store._fake_coll.update_one.call_args.args[1]
        set_doc = update_arg.get("$set", {})
        assert set_doc.get("child_count") == 4
        assert set_doc.get("succeeded_count") == 3
        assert set_doc.get("failed_count") == 1

    @pytest.mark.asyncio
    async def test_completed_at_is_datetime(self):
        store = _make_store()
        await store.write_completion(
            app_id="app1",
            parent_chat_id="chat1",
            trigger_id="t1",
            mfj_cycle=1,
            child_count=1,
            succeeded_count=1,
            failed_count=0,
            child_chat_ids=["c1"],
        )
        set_doc = store._fake_coll.update_one.call_args.args[1]["$set"]
        assert isinstance(set_doc.get("completed_at"), datetime)

    @pytest.mark.asyncio
    async def test_merge_summary_preview_included_when_provided(self):
        store = _make_store()
        await store.write_completion(
            app_id="a",
            parent_chat_id="p",
            trigger_id="t",
            mfj_cycle=1,
            child_count=1,
            succeeded_count=1,
            failed_count=0,
            child_chat_ids=["c"],
            merge_summary_preview={"keys": ["result", "score"]},
        )
        set_doc = store._fake_coll.update_one.call_args.args[1]["$set"]
        assert set_doc.get("merge_summary_preview") == {"keys": ["result", "score"]}

    @pytest.mark.asyncio
    async def test_merge_summary_defaults_to_empty_dict_when_none(self):
        store = _make_store()
        await store.write_completion(
            app_id="a",
            parent_chat_id="p",
            trigger_id="t",
            mfj_cycle=1,
            child_count=1,
            succeeded_count=1,
            failed_count=0,
            child_chat_ids=["c"],
            merge_summary_preview=None,
        )
        set_doc = store._fake_coll.update_one.call_args.args[1].get("$set", {})
        # None is coerced to {} by `merge_summary_preview or {}`
        assert set_doc.get("merge_summary_preview") == {}


# ---------------------------------------------------------------------------
# load_completed_trigger_ids
# ---------------------------------------------------------------------------


class TestLoadCompletedTriggerIds:
    @pytest.mark.asyncio
    async def test_empty_collection_returns_empty_set(self):
        store = _make_store(docs=[])
        result = await store.load_completed_trigger_ids(
            app_id="app1", parent_chat_id="chat1"
        )
        assert result == set()

    @pytest.mark.asyncio
    async def test_returns_trigger_ids_for_parent(self):
        docs = [
            {"app_id": "app1", "parent_chat_id": "chat1", "trigger_id": "t_alpha"},
            {"app_id": "app1", "parent_chat_id": "chat1", "trigger_id": "t_beta"},
        ]
        store = _make_store(docs=docs)
        result = await store.load_completed_trigger_ids(
            app_id="app1", parent_chat_id="chat1"
        )
        assert result == {"t_alpha", "t_beta"}

    @pytest.mark.asyncio
    async def test_returns_set_not_list(self):
        docs = [{"app_id": "a", "parent_chat_id": "p", "trigger_id": "t1"}]
        store = _make_store(docs=docs)
        result = await store.load_completed_trigger_ids(app_id="a", parent_chat_id="p")
        assert isinstance(result, set)

    @pytest.mark.asyncio
    async def test_multiple_triggers_deduplicated(self):
        # In theory unique index prevents dups, but test the return is a set
        docs = [
            {"app_id": "a", "parent_chat_id": "p", "trigger_id": "t1"},
            {"app_id": "a", "parent_chat_id": "p", "trigger_id": "t1"},
        ]
        store = _make_store(docs=docs)
        result = await store.load_completed_trigger_ids(app_id="a", parent_chat_id="p")
        assert result == {"t1"}


# ---------------------------------------------------------------------------
# load_completions_for_parents — batch variant
# ---------------------------------------------------------------------------


class TestLoadCompletionsForParents:
    @pytest.mark.asyncio
    async def test_empty_parent_ids_returns_empty_dict(self):
        store = _make_store()
        result = await store.load_completions_for_parents(app_id="app1", parent_chat_ids=[])
        assert result == {}

    @pytest.mark.asyncio
    async def test_groups_by_parent_chat_id(self):
        docs = [
            {"app_id": "app1", "parent_chat_id": "parent_A", "trigger_id": "t1"},
            {"app_id": "app1", "parent_chat_id": "parent_A", "trigger_id": "t2"},
            {"app_id": "app1", "parent_chat_id": "parent_B", "trigger_id": "t3"},
        ]
        store = _make_store(docs=docs)
        result = await store.load_completions_for_parents(
            app_id="app1", parent_chat_ids=["parent_A", "parent_B"]
        )
        assert result.get("parent_A") == {"t1", "t2"}
        assert result.get("parent_B") == {"t3"}

    @pytest.mark.asyncio
    async def test_parent_with_no_completions_mapped_to_empty_set(self):
        store = _make_store(docs=[])
        result = await store.load_completions_for_parents(
            app_id="app1", parent_chat_ids=["parent_X"]
        )
        # Either absent or empty
        assert result.get("parent_X", set()) == set()


# ---------------------------------------------------------------------------
# load_recent_parent_ids
# ---------------------------------------------------------------------------


class TestLoadRecentParentIds:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        docs = [
            {"app_id": "app1", "parent_chat_id": "chat_A", "completed_at": datetime.now(timezone.utc)},
            {"app_id": "app1", "parent_chat_id": "chat_B", "completed_at": datetime.now(timezone.utc)},
        ]
        store = _make_store(docs=docs)
        result = await store.load_recent_parent_ids(app_id="app1")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self):
        store = _make_store(docs=[])
        result = await store.load_recent_parent_ids(app_id="app1")
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplicates_parent_ids(self):
        docs = [
            {"app_id": "app1", "parent_chat_id": "chat_A", "trigger_id": "t1",
             "completed_at": datetime.now(timezone.utc)},
            {"app_id": "app1", "parent_chat_id": "chat_A", "trigger_id": "t2",
             "completed_at": datetime.now(timezone.utc)},
            {"app_id": "app1", "parent_chat_id": "chat_B", "trigger_id": "t3",
             "completed_at": datetime.now(timezone.utc)},
        ]
        store = _make_store(docs=docs)
        result = await store.load_recent_parent_ids(app_id="app1")
        # chat_A should appear only once despite two trigger docs
        assert result.count("chat_A") <= 1


# ---------------------------------------------------------------------------
# Recovery scenario: coordinator re-hydrates from store
# ---------------------------------------------------------------------------


class TestRecoveryScenario:
    """Simulate a coordinator restart scenario where completed MFJ cycles
    are recovered from the persistence store into the in-memory cache."""

    @pytest.mark.asyncio
    async def test_coordinator_hydrates_from_store_on_first_requires_check(self, mocker):
        from tests.import_utils import import_module_directly as _imp

        _coord_mod = _imp("mozaiksai.core.workflow.pack.workflow_pack_coordinator")
        WorkflowPackCoordinator = _coord_mod.WorkflowPackCoordinator

        coord = WorkflowPackCoordinator()
        # Simulate store returning previously-persisted completions
        mocker.patch.object(
            coord._completion_store,
            "load_completed_trigger_ids",
            return_value={"trigger_step_1", "trigger_step_2"},
        )
        result = await coord._check_requires("app1", "parent_A", ["trigger_step_1"])
        assert result is True

    @pytest.mark.asyncio
    async def test_coordinator_requires_fails_when_store_empty_after_restart(self, mocker):
        from tests.import_utils import import_module_directly as _imp

        _coord_mod = _imp("mozaiksai.core.workflow.pack.workflow_pack_coordinator")
        WorkflowPackCoordinator = _coord_mod.WorkflowPackCoordinator

        coord = WorkflowPackCoordinator()
        mocker.patch.object(
            coord._completion_store,
            "load_completed_trigger_ids",
            return_value=set(),
        )
        result = await coord._check_requires("app1", "parent_new", ["trigger_step_1"])
        assert result is False

    @pytest.mark.asyncio
    async def test_subsequent_requires_check_uses_memory_cache(self, mocker):
        """After the first load, _check_requires must not call the store again."""
        from tests.import_utils import import_module_directly as _imp

        _coord_mod = _imp("mozaiksai.core.workflow.pack.workflow_pack_coordinator")
        WorkflowPackCoordinator = _coord_mod.WorkflowPackCoordinator

        coord = WorkflowPackCoordinator()
        load_mock = mocker.patch.object(
            coord._completion_store,
            "load_completed_trigger_ids",
            return_value={"t1"},
        )
        # First call — loads from store
        await coord._check_requires("app1", "parent1", ["t1"])
        first_call_count = load_mock.call_count
        # Second call — should use in-memory cache
        await coord._check_requires("app1", "parent1", ["t1"])
        assert load_mock.call_count == first_call_count

    @pytest.mark.asyncio
    async def test_in_memory_completion_survives_check_requires_call(self):
        from tests.import_utils import import_module_directly as _imp

        _coord_mod = _imp("mozaiksai.core.workflow.pack.workflow_pack_coordinator")
        WorkflowPackCoordinator = _coord_mod.WorkflowPackCoordinator

        coord = WorkflowPackCoordinator()
        # Directly seed in-memory state (simulates previous cycle completing in same process)
        coord._completed_mfjs["parent_live"] = {"trigger_live"}
        result = await coord._check_requires("app1", "parent_live", ["trigger_live"])
        assert result is True

    def test_completed_mfjs_starts_empty(self):
        from tests.import_utils import import_module_directly as _imp

        _coord_mod = _imp("mozaiksai.core.workflow.pack.workflow_pack_coordinator")
        coord = _coord_mod.WorkflowPackCoordinator()
        assert coord._completed_mfjs == {}
