"""
Pure helper unit tests for:
  mozaiksai/core/session/persistence.py

Covers sync pure helpers (no IO/async):

  _coerce_datetime:
    - datetime instance → returned as-is
    - ISO string with timezone → parsed
    - ISO string without timezone → UTC injected
    - invalid string → fallback returned
    - None → fallback returned
    - int → fallback returned

  _coerce_string_map:
    - non-dict → {}
    - empty dict → {}
    - dict with string values → normalized
    - dict with None keys filtered
    - dict with empty values filtered
    - dict with None values filtered
    - keys and values stripped

  _coerce_dict:
    - dict → returned as new dict
    - None → {}
    - list → {}
    - string → {}

  _coerce_string_list:
    - non-list → []
    - empty list → []
    - list of strings → stripped, non-empty kept
    - None entries filtered
    - empty strings filtered
    - whitespace-only entries filtered

  _default_sequence_status:
    - COMPLETED lifecycle → COMPLETED status
    - STALE lifecycle → STALE status
    - ACTIVE lifecycle → IN_PROGRESS status
    - INITIAL lifecycle → IN_PROGRESS status
    - AWAITING_TRANSITION lifecycle → IN_PROGRESS status
"""
from __future__ import annotations

from datetime import UTC, datetime

from mozaiksai.core.session.model import SequenceStatus, SessionLifecycle
from mozaiksai.core.session.persistence import (
    _coerce_datetime,
    _coerce_dict,
    _coerce_string_list,
    _coerce_string_map,
    _default_sequence_status,
)

_FALLBACK = datetime(2026, 6, 12, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. _coerce_datetime
# ---------------------------------------------------------------------------

class TestCoerceDatetime:
    def test_datetime_instance_returned_unchanged(self):
        dt = datetime(2026, 1, 1, tzinfo=UTC)
        result = _coerce_datetime(dt, fallback=_FALLBACK)
        assert result is dt

    def test_iso_string_with_tz_parsed(self):
        result = _coerce_datetime("2026-03-15T10:00:00+00:00", fallback=_FALLBACK)
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 15
        assert result.tzinfo is not None

    def test_iso_string_without_tz_gets_utc(self):
        result = _coerce_datetime("2026-03-15T10:00:00", fallback=_FALLBACK)
        assert result.year == 2026
        assert result.tzinfo == UTC

    def test_invalid_string_returns_fallback(self):
        result = _coerce_datetime("not-a-date", fallback=_FALLBACK)
        assert result is _FALLBACK

    def test_none_returns_fallback(self):
        result = _coerce_datetime(None, fallback=_FALLBACK)
        assert result is _FALLBACK

    def test_integer_returns_fallback(self):
        result = _coerce_datetime(1234567890, fallback=_FALLBACK)
        assert result is _FALLBACK

    def test_empty_string_returns_fallback(self):
        result = _coerce_datetime("", fallback=_FALLBACK)
        assert result is _FALLBACK


# ---------------------------------------------------------------------------
# 2. _coerce_string_map
# ---------------------------------------------------------------------------

class TestCoerceStringMap:
    def test_non_dict_returns_empty(self):
        assert _coerce_string_map(None) == {}
        assert _coerce_string_map("text") == {}
        assert _coerce_string_map([1, 2]) == {}

    def test_empty_dict_returns_empty(self):
        assert _coerce_string_map({}) == {}

    def test_dict_with_string_values_normalized(self):
        result = _coerce_string_map({"key": "value"})
        assert result == {"key": "value"}

    def test_keys_and_values_stripped(self):
        result = _coerce_string_map({"  key  ": "  value  "})
        assert result == {"key": "value"}

    def test_empty_key_filtered(self):
        result = _coerce_string_map({"": "value", "key": "good"})
        assert result == {"key": "good"}

    def test_empty_value_filtered(self):
        result = _coerce_string_map({"key": "", "key2": "good"})
        assert result == {"key2": "good"}

    def test_none_key_filtered(self):
        result = _coerce_string_map({None: "value", "key": "good"})
        assert "key" in result
        assert result.get("key") == "good"

    def test_none_value_filtered(self):
        result = _coerce_string_map({"key": None, "key2": "good"})
        assert result == {"key2": "good"}

    def test_numeric_values_coerced_to_string(self):
        result = _coerce_string_map({"key": 42})
        assert result == {"key": "42"}


# ---------------------------------------------------------------------------
# 3. _coerce_dict
# ---------------------------------------------------------------------------

class TestCoerceDict:
    def test_dict_returns_copy(self):
        d = {"a": 1, "b": 2}
        result = _coerce_dict(d)
        assert result == {"a": 1, "b": 2}

    def test_dict_copy_is_new_object(self):
        d = {"a": 1}
        result = _coerce_dict(d)
        assert result is not d

    def test_none_returns_empty(self):
        assert _coerce_dict(None) == {}

    def test_list_returns_empty(self):
        assert _coerce_dict([1, 2]) == {}

    def test_string_returns_empty(self):
        assert _coerce_dict("not-a-dict") == {}

    def test_empty_dict_returns_empty(self):
        assert _coerce_dict({}) == {}


# ---------------------------------------------------------------------------
# 4. _coerce_string_list
# ---------------------------------------------------------------------------

class TestCoerceStringList:
    def test_non_list_returns_empty(self):
        assert _coerce_string_list(None) == []
        assert _coerce_string_list("text") == []
        assert _coerce_string_list({"key": "val"}) == []

    def test_empty_list_returns_empty(self):
        assert _coerce_string_list([]) == []

    def test_list_of_strings_returned(self):
        result = _coerce_string_list(["alpha", "beta"])
        assert result == ["alpha", "beta"]

    def test_strings_stripped(self):
        result = _coerce_string_list(["  alpha  ", "  beta  "])
        assert result == ["alpha", "beta"]

    def test_none_entries_filtered(self):
        result = _coerce_string_list(["alpha", None, "beta"])
        assert result == ["alpha", "beta"]

    def test_empty_strings_filtered(self):
        result = _coerce_string_list(["alpha", "", "beta"])
        assert result == ["alpha", "beta"]

    def test_whitespace_only_filtered(self):
        result = _coerce_string_list(["alpha", "   ", "beta"])
        assert result == ["alpha", "beta"]

    def test_numeric_entries_coerced_to_string(self):
        result = _coerce_string_list([42, "beta"])
        assert result == ["42", "beta"]


# ---------------------------------------------------------------------------
# 5. _default_sequence_status
# ---------------------------------------------------------------------------

class TestDefaultSequenceStatus:
    def test_completed_lifecycle_returns_completed(self):
        result = _default_sequence_status(SessionLifecycle.COMPLETED)
        assert result == SequenceStatus.COMPLETED

    def test_stale_lifecycle_returns_stale(self):
        result = _default_sequence_status(SessionLifecycle.STALE)
        assert result == SequenceStatus.STALE

    def test_active_lifecycle_returns_in_progress(self):
        result = _default_sequence_status(SessionLifecycle.ACTIVE)
        assert result == SequenceStatus.IN_PROGRESS

    def test_initial_lifecycle_returns_in_progress(self):
        result = _default_sequence_status(SessionLifecycle.INITIAL)
        assert result == SequenceStatus.IN_PROGRESS

    def test_awaiting_transition_returns_in_progress(self):
        result = _default_sequence_status(SessionLifecycle.AWAITING_TRANSITION)
        assert result == SequenceStatus.IN_PROGRESS

    def test_awaiting_decision_returns_in_progress(self):
        result = _default_sequence_status(SessionLifecycle.AWAITING_DECISION)
        assert result == SequenceStatus.IN_PROGRESS
