"""
Admin router pure helper unit tests.

Covers:
  _serialize_datetime:
    - datetime object → isoformat string
    - date object with isoformat → string
    - None → None
    - string (no isoformat attr) → None
    - int → None
    - object where isoformat raises → None

  _count_assistant_turns:
    - empty list → 0
    - not a list → 0
    - list with no assistant messages → 0
    - list with one assistant message → 1
    - list with multiple assistant messages → count
    - non-dict items in list skipped
    - role != "assistant" not counted

  _compute_session_runtime_sec:
    - doc with created_at and completed_at → time delta used if > stored
    - doc with completed_at < stored → stored returned
    - stored_duration fallback when no created_at
    - in-progress session with created_at and now → elapsed used
    - no created_at → stored_duration returned
    - naive created_at/completed_at → treated as UTC
    - exception in calculation → stored_duration returned
"""
from __future__ import annotations

from datetime import UTC, datetime

from mozaiksai.core.admin.router import (
    _compute_session_runtime_sec,
    _count_assistant_turns,
    _serialize_datetime,
)
from mozaiksai.core.data.models import WorkflowStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(year=2024, month=6, day=1, hour=0, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. _serialize_datetime
# ---------------------------------------------------------------------------

class TestSerializeDatetime:
    def test_datetime_returns_isoformat(self):
        dt = _utc(2024, 6, 1, 12, 30, 0)
        result = _serialize_datetime(dt)
        assert isinstance(result, str)
        assert "2024-06-01" in result

    def test_none_returns_none(self):
        assert _serialize_datetime(None) is None

    def test_string_returns_none(self):
        assert _serialize_datetime("2024-06-01") is None

    def test_int_returns_none(self):
        assert _serialize_datetime(1234567890) is None

    def test_object_where_isoformat_raises_returns_none(self):
        class Broken:
            def isoformat(self):
                raise RuntimeError("boom")

        assert _serialize_datetime(Broken()) is None

    def test_naive_datetime_has_isoformat(self):
        naive_dt = datetime(2024, 6, 1, 12, 0, 0)
        result = _serialize_datetime(naive_dt)
        assert isinstance(result, str)
        assert "2024-06-01" in result


# ---------------------------------------------------------------------------
# 2. _count_assistant_turns
# ---------------------------------------------------------------------------

class TestCountAssistantTurns:
    def test_empty_list_returns_zero(self):
        assert _count_assistant_turns([]) == 0

    def test_not_a_list_returns_zero(self):
        assert _count_assistant_turns("messages") == 0
        assert _count_assistant_turns(None) == 0
        assert _count_assistant_turns(42) == 0

    def test_no_assistant_messages_returns_zero(self):
        messages = [{"role": "user", "content": "hello"}]
        assert _count_assistant_turns(messages) == 0

    def test_one_assistant_message(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        assert _count_assistant_turns(messages) == 1

    def test_multiple_assistant_messages(self):
        messages = [
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
            {"role": "assistant", "content": "d"},
        ]
        assert _count_assistant_turns(messages) == 3

    def test_non_dict_items_skipped(self):
        messages = ["not_a_dict", {"role": "assistant", "content": "hi"}]
        assert _count_assistant_turns(messages) == 1

    def test_system_role_not_counted(self):
        messages = [{"role": "system", "content": "prompt"}]
        assert _count_assistant_turns(messages) == 0


# ---------------------------------------------------------------------------
# 3. _compute_session_runtime_sec
# ---------------------------------------------------------------------------

class TestComputeSessionRuntimeSec:
    def test_uses_stored_duration_when_no_timestamps(self):
        doc = {"duration_sec": 45.0}
        now = _utc()
        result = _compute_session_runtime_sec(doc, now=now)
        assert result == 45.0

    def test_created_and_completed_at_used_for_delta(self):
        start = _utc(2024, 6, 1, 12, 0, 0)
        end = _utc(2024, 6, 1, 12, 0, 30)  # 30 seconds later
        doc = {"created_at": start, "completed_at": end, "duration_sec": 0.0}
        now = _utc(2024, 6, 1, 13, 0, 0)
        result = _compute_session_runtime_sec(doc, now=now)
        assert result == 30.0

    def test_stored_duration_wins_when_larger(self):
        start = _utc(2024, 6, 1, 12, 0, 0)
        end = _utc(2024, 6, 1, 12, 0, 30)  # 30s delta
        doc = {"created_at": start, "completed_at": end, "duration_sec": 60.0}
        now = _utc(2024, 6, 1, 13, 0, 0)
        result = _compute_session_runtime_sec(doc, now=now)
        # max(60.0, 30.0) = 60.0
        assert result == 60.0

    def test_in_progress_uses_now_minus_created_at(self):
        start = _utc(2024, 6, 1, 12, 0, 0)
        now = _utc(2024, 6, 1, 12, 1, 0)  # 60 seconds later
        doc = {
            "created_at": start,
            "duration_sec": 0.0,
            "status": int(WorkflowStatus.IN_PROGRESS),
        }
        result = _compute_session_runtime_sec(doc, now=now)
        assert result == 60.0

    def test_naive_created_at_treated_as_utc(self):
        naive_start = datetime(2024, 6, 1, 12, 0, 0)  # no tzinfo
        naive_end = datetime(2024, 6, 1, 12, 0, 45)   # 45s later
        doc = {"created_at": naive_start, "completed_at": naive_end, "duration_sec": 0.0}
        now = _utc(2024, 6, 1, 13, 0, 0)
        result = _compute_session_runtime_sec(doc, now=now)
        assert result == 45.0

    def test_no_created_at_returns_stored(self):
        doc = {"duration_sec": 20.0}
        now = _utc()
        assert _compute_session_runtime_sec(doc, now=now) == 20.0

    def test_missing_duration_sec_returns_zero(self):
        doc = {}
        now = _utc()
        assert _compute_session_runtime_sec(doc, now=now) == 0.0
