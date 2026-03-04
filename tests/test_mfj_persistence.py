"""
Tests for MFJ Persistence — MFJCompletionStore + coordinator write-through/read-through
========================================================================================

Covers:
1. MFJCompletionStore with mocked MongoDB (AsyncMock Motor collections)
2. Coordinator write-through (record → write to store)
3. Coordinator read-through (check requires → fallback to store on cache miss)
4. Recovery: rebuild in-memory cache from MongoDB on restart
5. Graceful degradation: MongoDB unavailable → in-memory-only operation
6. Index creation idempotency
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Import machinery (same approach as test_workflow_pack_coordinator.py)
# ---------------------------------------------------------------------------
import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _direct_import(module_name: str, file_path: Path):
    """Import a single .py file as module_name, registering parent stubs."""
    parts = module_name.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            m = types.ModuleType(parent)
            m.__path__ = []
            m.__package__ = parent
            sys.modules[parent] = m
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Pre-register namespace stubs
for _ns in [
    "mozaiksai",
    "mozaiksai.core",
    "mozaiksai.core.contracts",
    "mozaiksai.core.contracts.events",
    "mozaiksai.core.workflow",
    "mozaiksai.core.workflow.pack",
    "mozaiksai.orchestration",
]:
    if _ns not in sys.modules:
        _m = types.ModuleType(_ns)
        _m.__path__ = [str(_ROOT / _ns.replace(".", "/"))]
        _m.__package__ = _ns
        sys.modules[_ns] = _m

# Import contracts.events (needed by orchestration modules)
if "mozaiksai.core.contracts.events" not in sys.modules or \
   not hasattr(sys.modules["mozaiksai.core.contracts.events"], "DomainEvent"):
    _direct_import(
        "mozaiksai.core.contracts.events",
        _ROOT / "mozaiksai" / "core" / "contracts" / "events.py",
    )

# Import orchestration modules
if "mozaiksai.orchestration.decomposition" not in sys.modules or \
   not hasattr(sys.modules.get("mozaiksai.orchestration.decomposition", None), "SubTask"):
    _direct_import(
        "mozaiksai.orchestration.decomposition",
        _ROOT / "mozaiksai" / "orchestration" / "decomposition.py",
    )

if "mozaiksai.orchestration.merge" not in sys.modules or \
   not hasattr(sys.modules.get("mozaiksai.orchestration.merge", None), "ChildResult"):
    _direct_import(
        "mozaiksai.orchestration.merge",
        _ROOT / "mozaiksai" / "orchestration" / "merge.py",
    )

# Import schema module (needed by coordinator's _resolve_triggers)
if "mozaiksai.core.workflow.pack.schema" not in sys.modules or \
   not hasattr(sys.modules.get("mozaiksai.core.workflow.pack.schema", None), "MidFlightJourney"):
    _direct_import(
        "mozaiksai.core.workflow.pack.schema",
        _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "schema.py",
    )

# Import mfj_persistence
_persist_mod = _direct_import(
    "mozaiksai.core.workflow.pack.mfj_persistence",
    _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "mfj_persistence.py",
)
MFJCompletionStore = _persist_mod.MFJCompletionStore

# Import coordinator
_coord_mod = _direct_import(
    "mozaiksai.core.workflow.pack.workflow_pack_coordinator",
    _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "workflow_pack_coordinator.py",
)
WorkflowPackCoordinator = _coord_mod.WorkflowPackCoordinator
_MFJCompletionRecord = _coord_mod._MFJCompletionRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_mock_collection(docs: List[Dict[str, Any]] | None = None):
    """Create a mock Motor collection with configurable find/insert behavior."""
    coll = AsyncMock()
    stored_docs = list(docs or [])

    # insert_one
    async def _insert(doc):
        stored_docs.append(dict(doc))
        result = MagicMock()
        result.inserted_id = "mock_id"
        return result

    coll.insert_one = AsyncMock(side_effect=_insert)

    # find: returns an async mock with to_list
    def _find(query=None, projection=None):
        cursor = AsyncMock()
        matched = []
        for d in stored_docs:
            if query is None:
                matched.append(d)
            else:
                match = True
                for k, v in query.items():
                    if k == "$in" or isinstance(v, dict):
                        if "$in" in v:
                            if d.get(k) not in v["$in"]:
                                match = False
                        elif "$gte" in v:
                            if d.get(k, datetime.min.replace(tzinfo=timezone.utc)) < v["$gte"]:
                                match = False
                    elif d.get(k) != v:
                        match = False
                if match:
                    # Apply projection
                    if projection:
                        filtered = {}
                        for pk, pv in projection.items():
                            if pk == "_id" and pv == 0:
                                continue
                            if pv == 1 and pk in d:
                                filtered[pk] = d[pk]
                        matched.append(filtered if filtered else d)
                    else:
                        matched.append(d)

        cursor.to_list = AsyncMock(return_value=matched)
        return cursor

    coll.find = MagicMock(side_effect=_find)

    # aggregate: returns an async cursor
    def _aggregate(pipeline):
        cursor = AsyncMock()
        # Simple aggregation: just return unique parent_chat_ids
        parent_ids = set()
        for d in stored_docs:
            parent_ids.add(d.get("parent_chat_id"))
        cursor.to_list = AsyncMock(
            return_value=[{"_id": pid} for pid in parent_ids if pid]
        )
        return cursor

    coll.aggregate = MagicMock(side_effect=_aggregate)

    # list_indexes
    coll.list_indexes = MagicMock(return_value=AsyncMock(
        to_list=AsyncMock(return_value=[])
    ))

    # create_index
    coll.create_index = AsyncMock()

    # Expose stored docs for assertions
    coll._stored_docs = stored_docs

    return coll


def _make_store_with_mock_coll(docs=None, ttl_days=7):
    """Create an MFJCompletionStore with a pre-injected mock collection."""
    store = MFJCompletionStore(ttl_days=ttl_days)
    mock_coll = _make_mock_collection(docs)
    # Bypass lazy client init — inject the collection directly
    store._get_collection = MagicMock(return_value=mock_coll)
    return store, mock_coll


# ===========================================================================
# 1. MFJCompletionStore — Index Management
# ===========================================================================

class TestMFJStoreIndexes:
    """Test index creation on the MFJCompletions collection."""

    @pytest.mark.asyncio
    async def test_ensure_indexes_creates_both(self):
        """First call creates compound + TTL indexes."""
        store, coll = _make_store_with_mock_coll()
        await store.ensure_indexes()

        assert coll.create_index.call_count == 2
        # Check compound index
        compound_call = coll.create_index.call_args_list[0]
        assert ("parent_chat_id", 1) in compound_call.args[0]
        assert ("trigger_id", 1) in compound_call.args[0]
        # Check TTL index
        ttl_call = coll.create_index.call_args_list[1]
        assert ttl_call.args[0] == "completed_at"
        assert ttl_call.kwargs["expireAfterSeconds"] == 7 * 86400

    @pytest.mark.asyncio
    async def test_ensure_indexes_idempotent(self):
        """Second call is a no-op (skips creation)."""
        store, coll = _make_store_with_mock_coll()
        await store.ensure_indexes()
        await store.ensure_indexes()
        # Still only 2 create_index calls from the first ensure
        assert coll.create_index.call_count == 2

    @pytest.mark.asyncio
    async def test_ensure_indexes_skips_existing(self):
        """If indexes already exist, skip creation."""
        store, coll = _make_store_with_mock_coll()
        # Simulate existing indexes
        coll.list_indexes = MagicMock(return_value=AsyncMock(
            to_list=AsyncMock(return_value=[
                {"name": "mfj_parent_trigger"},
                {"name": "mfj_ttl_completed_at"},
            ])
        ))
        await store.ensure_indexes()
        assert coll.create_index.call_count == 0

    @pytest.mark.asyncio
    async def test_ensure_indexes_mongo_unavailable(self):
        """If collection returns None, indexes are skipped gracefully."""
        store = MFJCompletionStore()
        store._get_collection = MagicMock(return_value=None)
        await store.ensure_indexes()  # Should not raise


# ===========================================================================
# 2. MFJCompletionStore — Write
# ===========================================================================

class TestMFJStoreWrite:
    """Test writing completion records to MongoDB."""

    @pytest.mark.asyncio
    async def test_write_completion_success(self):
        store, coll = _make_store_with_mock_coll()
        result = await store.write_completion(
            parent_chat_id="parent1",
            trigger_id="trigger_a",
            completed_at=_now(),
            child_count=3,
            all_succeeded=True,
            child_chat_ids=["c1", "c2", "c3"],
            merge_summary_preview="all good",
        )
        assert result is True
        assert coll.insert_one.call_count == 1
        inserted = coll._stored_docs[-1]
        assert inserted["parent_chat_id"] == "parent1"
        assert inserted["trigger_id"] == "trigger_a"
        assert inserted["child_count"] == 3
        assert inserted["child_chat_ids"] == ["c1", "c2", "c3"]

    @pytest.mark.asyncio
    async def test_write_completion_truncates_summary(self):
        store, coll = _make_store_with_mock_coll()
        long_summary = "x" * 1000
        await store.write_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            completed_at=_now(),
            child_count=1,
            all_succeeded=True,
            merge_summary_preview=long_summary,
        )
        inserted = coll._stored_docs[-1]
        assert len(inserted["merge_summary_preview"]) == 500

    @pytest.mark.asyncio
    async def test_write_completion_mongo_unavailable(self):
        store = MFJCompletionStore()
        store._get_collection = MagicMock(return_value=None)
        result = await store.write_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            completed_at=_now(),
            child_count=1,
            all_succeeded=True,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_write_completion_insert_error(self):
        store, coll = _make_store_with_mock_coll()
        coll.insert_one = AsyncMock(side_effect=Exception("write error"))
        result = await store.write_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            completed_at=_now(),
            child_count=1,
            all_succeeded=True,
        )
        assert result is False


# ===========================================================================
# 3. MFJCompletionStore — Read
# ===========================================================================

class TestMFJStoreRead:
    """Test reading completion records from MongoDB."""

    @pytest.mark.asyncio
    async def test_load_completed_trigger_ids(self):
        docs = [
            {"parent_chat_id": "p1", "trigger_id": "t1"},
            {"parent_chat_id": "p1", "trigger_id": "t2"},
            {"parent_chat_id": "p2", "trigger_id": "t3"},  # different parent
        ]
        store, coll = _make_store_with_mock_coll(docs)
        ids = await store.load_completed_trigger_ids("p1")
        assert ids == {"t1", "t2"}

    @pytest.mark.asyncio
    async def test_load_completed_trigger_ids_empty(self):
        store, coll = _make_store_with_mock_coll([])
        ids = await store.load_completed_trigger_ids("p1")
        assert ids == set()

    @pytest.mark.asyncio
    async def test_load_completed_trigger_ids_mongo_unavailable(self):
        store = MFJCompletionStore()
        store._get_collection = MagicMock(return_value=None)
        ids = await store.load_completed_trigger_ids("p1")
        assert ids == set()

    @pytest.mark.asyncio
    async def test_load_completions_for_parents(self):
        docs = [
            {"parent_chat_id": "p1", "trigger_id": "t1", "child_count": 2},
            {"parent_chat_id": "p1", "trigger_id": "t2", "child_count": 1},
            {"parent_chat_id": "p2", "trigger_id": "t3", "child_count": 3},
        ]
        store, coll = _make_store_with_mock_coll(docs)
        result = await store.load_completions_for_parents(["p1", "p2"])
        assert "p1" in result
        assert "p2" in result
        assert len(result["p1"]) == 2
        assert len(result["p2"]) == 1

    @pytest.mark.asyncio
    async def test_load_completions_for_parents_empty_input(self):
        store, coll = _make_store_with_mock_coll()
        result = await store.load_completions_for_parents([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_load_paused_parent_ids(self):
        docs = [
            {"parent_chat_id": "p1", "trigger_id": "t1", "completed_at": _now()},
            {"parent_chat_id": "p2", "trigger_id": "t2", "completed_at": _now()},
        ]
        store, coll = _make_store_with_mock_coll(docs)
        ids = await store.load_paused_parent_ids()
        assert set(ids) == {"p1", "p2"}


# ===========================================================================
# 4. Coordinator Write-Through
# ===========================================================================

class TestCoordinatorWriteThrough:
    """Coordinator._record_mfj_completion writes to both cache and store."""

    @pytest.mark.asyncio
    async def test_record_writes_to_store(self):
        store, coll = _make_store_with_mock_coll()
        c = WorkflowPackCoordinator(mfj_store=store)
        await c._record_mfj_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            child_count=2,
            all_succeeded=True,
            child_chat_ids=["c1", "c2"],
            merge_summary_preview="ok",
        )
        # In-memory cache updated
        assert len(c._completed_mfjs["p1"]) == 1
        assert c._completed_mfjs["p1"][0].trigger_id == "t1"
        # MongoDB store written
        assert coll.insert_one.call_count == 1

    @pytest.mark.asyncio
    async def test_record_without_store_still_works(self):
        """With mfj_store=None, only in-memory cache is updated."""
        c = WorkflowPackCoordinator()
        await c._record_mfj_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            child_count=1,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        assert len(c._completed_mfjs["p1"]) == 1

    @pytest.mark.asyncio
    async def test_record_store_failure_doesnt_block(self):
        """Store write failure doesn't prevent in-memory recording."""
        store, coll = _make_store_with_mock_coll()
        coll.insert_one = AsyncMock(side_effect=Exception("boom"))
        c = WorkflowPackCoordinator(mfj_store=store)
        await c._record_mfj_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            child_count=1,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        # In-memory still works
        assert len(c._completed_mfjs["p1"]) == 1


# ===========================================================================
# 5. Coordinator Read-Through
# ===========================================================================

class TestCoordinatorReadThrough:
    """Coordinator._check_mfj_requires falls back to store on cache miss."""

    @pytest.mark.asyncio
    async def test_cache_hit_no_store_call(self):
        """When all requires are in cache, no store read happens."""
        store, coll = _make_store_with_mock_coll()
        c = WorkflowPackCoordinator(mfj_store=store)
        # Pre-populate cache
        await c._record_mfj_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            child_count=1,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        # Reset find call tracking (write_completion doesn't call find)
        coll.find.reset_mock()
        result = await c._check_mfj_requires("p1", ["t1"])
        assert result is True
        # Store's find should NOT have been called (cache hit)
        coll.find.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_reads_from_store(self):
        """When requires not in cache, reads from store."""
        docs = [
            {"parent_chat_id": "p1", "trigger_id": "t1"},
        ]
        store, coll = _make_store_with_mock_coll(docs)
        c = WorkflowPackCoordinator(mfj_store=store)
        # Cache is empty — should fall through to store
        result = await c._check_mfj_requires("p1", ["t1"])
        assert result is True
        # Verify cache was populated from store
        assert len(c._completed_mfjs["p1"]) == 1
        assert c._completed_mfjs["p1"][0].trigger_id == "t1"

    @pytest.mark.asyncio
    async def test_cache_miss_store_also_missing(self):
        """When requires not in cache or store, returns False."""
        store, coll = _make_store_with_mock_coll([])
        c = WorkflowPackCoordinator(mfj_store=store)
        result = await c._check_mfj_requires("p1", ["t1"])
        assert result is False

    @pytest.mark.asyncio
    async def test_cache_miss_store_error_graceful(self):
        """Store read error falls back to in-memory only."""
        store = MFJCompletionStore()
        # Make load_completed_trigger_ids raise
        store.load_completed_trigger_ids = AsyncMock(side_effect=Exception("read fail"))
        # Need _get_collection to return non-None so store isn't skipped
        store._get_collection = MagicMock(return_value=MagicMock())
        c = WorkflowPackCoordinator(mfj_store=store)
        # Should not raise, just return False
        result = await c._check_mfj_requires("p1", ["t1"])
        assert result is False

    @pytest.mark.asyncio
    async def test_partial_cache_hit_reads_store_for_rest(self):
        """When some requires in cache but not all, reads store for remainder."""
        docs = [
            {"parent_chat_id": "p1", "trigger_id": "t2"},
        ]
        store, coll = _make_store_with_mock_coll(docs)
        c = WorkflowPackCoordinator(mfj_store=store)
        # Pre-populate cache with t1 only
        await c._record_mfj_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            child_count=1,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        # Need both t1 and t2 — t2 only in store
        result = await c._check_mfj_requires("p1", ["t1", "t2"])
        assert result is True


# ===========================================================================
# 6. Recovery on Restart
# ===========================================================================

class TestRecoveryFromPersistence:
    """Test coordinator.recover_from_persistence() rebuilds cache."""

    @pytest.mark.asyncio
    async def test_recovery_populates_cache(self):
        docs = [
            {
                "parent_chat_id": "p1",
                "trigger_id": "t1",
                "completed_at": _now(),
                "child_count": 2,
                "all_succeeded": True,
                "merge_summary_preview": "ok",
            },
            {
                "parent_chat_id": "p1",
                "trigger_id": "t2",
                "completed_at": _now(),
                "child_count": 1,
                "all_succeeded": False,
                "merge_summary_preview": "partial",
            },
        ]
        store, coll = _make_store_with_mock_coll(docs)
        c = WorkflowPackCoordinator(mfj_store=store)
        count = await c.recover_from_persistence()
        assert count == 1  # 1 parent recovered
        assert "p1" in c._completed_mfjs
        tids = {r.trigger_id for r in c._completed_mfjs["p1"]}
        assert tids == {"t1", "t2"}

    @pytest.mark.asyncio
    async def test_recovery_no_store(self):
        """Without store, recovery returns 0."""
        c = WorkflowPackCoordinator()
        count = await c.recover_from_persistence()
        assert count == 0

    @pytest.mark.asyncio
    async def test_recovery_no_data(self):
        store, coll = _make_store_with_mock_coll([])
        c = WorkflowPackCoordinator(mfj_store=store)
        count = await c.recover_from_persistence()
        assert count == 0

    @pytest.mark.asyncio
    async def test_recovery_deduplicates(self):
        """If cache already has a trigger, recovery doesn't duplicate it."""
        docs = [
            {
                "parent_chat_id": "p1",
                "trigger_id": "t1",
                "completed_at": _now(),
                "child_count": 2,
                "all_succeeded": True,
                "merge_summary_preview": "ok",
            },
        ]
        store, coll = _make_store_with_mock_coll(docs)
        c = WorkflowPackCoordinator(mfj_store=store)
        # Pre-populate cache with t1
        await c._record_mfj_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            child_count=2,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        count = await c.recover_from_persistence()
        # t1 already in cache — should NOT be duplicated
        assert len(c._completed_mfjs["p1"]) == 1

    @pytest.mark.asyncio
    async def test_recovery_error_graceful(self):
        """Recovery failure returns 0, doesn't crash."""
        store = MFJCompletionStore()
        store._get_collection = MagicMock(return_value=MagicMock())
        store.ensure_indexes = AsyncMock(side_effect=Exception("idx fail"))
        store.load_paused_parent_ids = AsyncMock(side_effect=Exception("fail"))
        c = WorkflowPackCoordinator(mfj_store=store)
        count = await c.recover_from_persistence()
        assert count == 0

    @pytest.mark.asyncio
    async def test_recovery_then_requires_check(self):
        """After recovery, requires checks work from recovered cache."""
        docs = [
            {
                "parent_chat_id": "p1",
                "trigger_id": "t1",
                "completed_at": _now(),
                "child_count": 2,
                "all_succeeded": True,
                "merge_summary_preview": "ok",
            },
        ]
        store, coll = _make_store_with_mock_coll(docs)
        c = WorkflowPackCoordinator(mfj_store=store)
        await c.recover_from_persistence()
        # Now check requires — t1 should be in cache from recovery
        result = await c._check_mfj_requires("p1", ["t1"])
        assert result is True


# ===========================================================================
# 7. Graceful Degradation
# ===========================================================================

class TestGracefulDegradation:
    """When MongoDB is unavailable, coordinator still works in-memory."""

    @pytest.mark.asyncio
    async def test_no_store_record_and_check(self):
        """Full cycle without any store — purely in-memory."""
        c = WorkflowPackCoordinator()  # No mfj_store
        await c._record_mfj_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            child_count=1,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        assert await c._check_mfj_requires("p1", ["t1"]) is True
        assert await c._check_mfj_requires("p1", ["t1", "t2"]) is False

    @pytest.mark.asyncio
    async def test_store_with_none_collection(self):
        """Store returns None from _get_collection → graceful fallback."""
        store = MFJCompletionStore()
        store._get_collection = MagicMock(return_value=None)

        c = WorkflowPackCoordinator(mfj_store=store)
        await c._record_mfj_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            child_count=1,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        # In-memory cache still works
        assert await c._check_mfj_requires("p1", ["t1"]) is True


# ===========================================================================
# 8. TTL Configuration
# ===========================================================================

class TestTTLConfiguration:
    """Verify TTL index creation uses correct expiry."""

    @pytest.mark.asyncio
    async def test_custom_ttl(self):
        store, coll = _make_store_with_mock_coll()
        store._ttl_days = 14
        await store.ensure_indexes()
        ttl_call = coll.create_index.call_args_list[1]
        assert ttl_call.kwargs["expireAfterSeconds"] == 14 * 86400

    @pytest.mark.asyncio
    async def test_default_ttl_is_7_days(self):
        store = MFJCompletionStore()
        assert store._ttl_days == 7
