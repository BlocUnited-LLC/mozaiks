"""
Unit tests for mozaiksai/control_plane/refinement_tracking.py.

Tests cover:
  record_refinement_event:
    - gracefully no-ops when MongoDB is not configured
    - writes expected fields to collection
    - caps intent and error strings
    - excludes None optional fields
    - handles collection insert failure without raising

  get_refinement_history:
    - returns empty list when MongoDB is not configured
    - passes correct query to collection
    - respects limit cap (200)
    - returns empty list on exception
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozaiksai.control_plane.refinement_tracking import (
    get_refinement_history,
    record_refinement_event,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_collection(*, insert_ok: bool = True, find_docs: list | None = None) -> MagicMock:
    coll = MagicMock()
    if insert_ok:
        coll.insert_one = AsyncMock(return_value=None)
    else:
        coll.insert_one = AsyncMock(side_effect=RuntimeError("mongo down"))

    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=find_docs or [])
    coll.find.return_value = cursor
    return coll


def _mock_get_collection(coll):
    """Return a patcher that makes _get_collection return coll."""
    return patch(
        "mozaiksai.control_plane.refinement_tracking._get_collection",
        return_value=coll,
    )


# ---------------------------------------------------------------------------
# record_refinement_event
# ---------------------------------------------------------------------------


class TestRecordRefinementEvent:
    @pytest.mark.asyncio
    async def test_noop_when_no_collection(self):
        with patch(
            "mozaiksai.control_plane.refinement_tracking._get_collection",
            return_value=None,
        ):
            # Should not raise
            await record_refinement_event(
                event_kind="request_received",
                request_id="req-1",
                app_id="app-1",
            )

    @pytest.mark.asyncio
    async def test_inserts_document(self):
        coll = _make_collection()
        with _mock_get_collection(coll):
            await record_refinement_event(
                event_kind="classified",
                request_id="req-1",
                app_id="app-1",
                change_class="patch",
                workflow_sequence="patch_apply",
                outcome="ok",
            )
        coll.insert_one.assert_awaited_once()
        doc = coll.insert_one.call_args[0][0]
        assert doc["event_kind"] == "classified"
        assert doc["request_id"] == "req-1"
        assert doc["app_id"] == "app-1"
        assert doc["change_class"] == "patch"
        assert doc["workflow_sequence"] == "patch_apply"
        assert doc["outcome"] == "ok"
        assert "created_at" in doc
        assert doc["_id"].startswith("rfe-")

    @pytest.mark.asyncio
    async def test_intent_capped_at_2000_chars(self):
        coll = _make_collection()
        with _mock_get_collection(coll):
            await record_refinement_event(
                event_kind="request_received",
                request_id="req-1",
                app_id="app-1",
                intent="x" * 5000,
            )
        doc = coll.insert_one.call_args[0][0]
        assert len(doc["intent"]) == 2000

    @pytest.mark.asyncio
    async def test_error_capped_at_1000_chars(self):
        coll = _make_collection()
        with _mock_get_collection(coll):
            await record_refinement_event(
                event_kind="failed",
                request_id="req-1",
                app_id="app-1",
                outcome="error",
                error="e" * 2000,
            )
        doc = coll.insert_one.call_args[0][0]
        assert len(doc["error"]) == 1000

    @pytest.mark.asyncio
    async def test_none_optional_fields_excluded(self):
        coll = _make_collection()
        with _mock_get_collection(coll):
            await record_refinement_event(
                event_kind="request_received",
                request_id="req-1",
                app_id="app-1",
            )
        doc = coll.insert_one.call_args[0][0]
        # None fields should be absent from the document
        for key in ("intent", "change_class", "workflow_sequence", "outcome", "error", "duration_ms", "metadata"):
            assert key not in doc

    @pytest.mark.asyncio
    async def test_insert_failure_does_not_raise(self):
        coll = _make_collection(insert_ok=False)
        with _mock_get_collection(coll):
            # Should swallow the exception silently
            await record_refinement_event(
                event_kind="request_received",
                request_id="req-1",
                app_id="app-1",
            )

    @pytest.mark.asyncio
    async def test_duration_ms_included_when_provided(self):
        coll = _make_collection()
        with _mock_get_collection(coll):
            await record_refinement_event(
                event_kind="completed",
                request_id="req-1",
                app_id="app-1",
                outcome="ok",
                duration_ms=250,
            )
        doc = coll.insert_one.call_args[0][0]
        assert doc["duration_ms"] == 250

    @pytest.mark.asyncio
    async def test_metadata_included_when_provided(self):
        coll = _make_collection()
        with _mock_get_collection(coll):
            await record_refinement_event(
                event_kind="completed",
                request_id="req-1",
                app_id="app-1",
                metadata={"token_count": 1234},
            )
        doc = coll.insert_one.call_args[0][0]
        assert doc["metadata"] == {"token_count": 1234}


# ---------------------------------------------------------------------------
# get_refinement_history
# ---------------------------------------------------------------------------


class TestGetRefinementHistory:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_collection(self):
        with patch(
            "mozaiksai.control_plane.refinement_tracking._get_collection",
            return_value=None,
        ):
            result = await get_refinement_history(app_id="app-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_queries_by_app_id(self):
        docs = [{"event_kind": "classified", "app_id": "app-1"}]
        coll = _make_collection(find_docs=docs)
        with _mock_get_collection(coll):
            result = await get_refinement_history(app_id="app-1")
        assert result == docs
        query = coll.find.call_args[0][0]
        assert query["app_id"] == "app-1"

    @pytest.mark.asyncio
    async def test_adds_request_id_filter_when_provided(self):
        coll = _make_collection(find_docs=[])
        with _mock_get_collection(coll):
            await get_refinement_history(app_id="app-1", request_id="req-1")
        query = coll.find.call_args[0][0]
        assert query["request_id"] == "req-1"

    @pytest.mark.asyncio
    async def test_limit_capped_at_200(self):
        coll = _make_collection(find_docs=[])
        with _mock_get_collection(coll):
            await get_refinement_history(app_id="app-1", limit=9999)
        cursor = coll.find.return_value
        cursor.limit.assert_called_with(200)

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        coll = MagicMock()
        coll.find.side_effect = RuntimeError("mongo down")
        with _mock_get_collection(coll):
            result = await get_refinement_history(app_id="app-1")
        assert result == []
