"""
Configured entitlement adapter pure helper unit tests.

Covers:
  _default_database_name:
    - no env vars → DEFAULT_APP_DATABASE_NAME
    - MOZAIKS_APP_DATA_DATABASE_NAME set → that value
    - MOZAIKS_APP_DATABASE_NAME set (first unset) → that value
    - MOZAIKS_APPS_DATABASE set (first two unset) → that value
    - value is whitespace only → falls back to DEFAULT_APP_DATABASE_NAME
    - priority: DATA > APP > APPS

  _field_value:
    - empty field_name → default
    - None field_name → default
    - simple key → record[key]
    - nested dotted path → record["a"]["b"]
    - missing top-level key → default
    - missing nested key → default
    - non-Mapping at intermediate level → default
    - default overrideable

  _parse_datetime:
    - None → None
    - blank string → None
    - invalid string → None
    - ISO string without tz → returns datetime with UTC tz
    - ISO string ending in "Z" → converted to +00:00 and parsed
    - ISO string with tz → normalized to UTC
    - datetime without tz → attaches UTC
    - datetime with non-UTC tz → converted to UTC
    - non-string non-datetime → None

  _capability_ids:
    - non-list → frozenset()
    - empty list → frozenset()
    - list of strings → frozenset of stripped strings
    - list of Mappings with capability_id → frozenset
    - list of Mappings without capability_id → empty entries ignored
    - mixed strings and mappings
    - empty string stripped out
    - whitespace-only strings ignored
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from mozaiksai.core.runtime.app.entitlements import (
    _capability_ids,
    _default_database_name,
    _field_value,
    _parse_datetime,
)
from mozaiksai.core.runtime.persistence.mongo import DEFAULT_APP_DATABASE_NAME

# ---------------------------------------------------------------------------
# 1. _default_database_name
# ---------------------------------------------------------------------------

_ENV_KEYS = (
    "MOZAIKS_APP_DATA_DATABASE_NAME",
    "MOZAIKS_APP_DATABASE_NAME",
    "MOZAIKS_APPS_DATABASE",
)


class TestDefaultDatabaseName:
    def _clear(self, monkeypatch):
        for key in _ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

    def test_no_env_vars_returns_default(self, monkeypatch):
        self._clear(monkeypatch)
        assert _default_database_name() == DEFAULT_APP_DATABASE_NAME

    def test_mozaiks_app_data_database_name_used(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("MOZAIKS_APP_DATA_DATABASE_NAME", "my_data_db")
        assert _default_database_name() == "my_data_db"

    def test_mozaiks_app_database_name_fallback(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("MOZAIKS_APP_DATABASE_NAME", "app_db")
        assert _default_database_name() == "app_db"

    def test_mozaiks_apps_database_last_fallback(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("MOZAIKS_APPS_DATABASE", "apps_db")
        assert _default_database_name() == "apps_db"

    def test_first_env_var_takes_priority(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("MOZAIKS_APP_DATA_DATABASE_NAME", "first")
        monkeypatch.setenv("MOZAIKS_APP_DATABASE_NAME", "second")
        monkeypatch.setenv("MOZAIKS_APPS_DATABASE", "third")
        assert _default_database_name() == "first"

    def test_whitespace_only_value_falls_back_to_default(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("MOZAIKS_APP_DATA_DATABASE_NAME", "   ")
        assert _default_database_name() == DEFAULT_APP_DATABASE_NAME


# ---------------------------------------------------------------------------
# 2. _field_value
# ---------------------------------------------------------------------------

class TestFieldValue:
    def test_none_field_name_returns_default(self):
        assert _field_value({"a": 1}, None) is None

    def test_empty_field_name_returns_default(self):
        assert _field_value({"a": 1}, "") is None

    def test_simple_key(self):
        assert _field_value({"name": "alice"}, "name") == "alice"

    def test_nested_dotted_path(self):
        record = {"user": {"profile": {"age": 30}}}
        assert _field_value(record, "user.profile.age") == 30

    def test_missing_top_level_key_returns_default(self):
        assert _field_value({}, "missing") is None

    def test_missing_nested_key_returns_default(self):
        record = {"user": {"name": "bob"}}
        assert _field_value(record, "user.profile") is None

    def test_non_mapping_at_intermediate_level_returns_default(self):
        record = {"user": "not_a_mapping"}
        assert _field_value(record, "user.name") is None

    def test_custom_default_returned(self):
        assert _field_value({}, "missing", default="fallback") == "fallback"

    def test_value_is_none_returned(self):
        assert _field_value({"x": None}, "x") is None

    def test_value_is_falsy_returned(self):
        assert _field_value({"count": 0}, "count") == 0

    def test_deeply_nested_three_levels(self):
        record = {"a": {"b": {"c": {"d": "deep"}}}}
        assert _field_value(record, "a.b.c.d") == "deep"


# ---------------------------------------------------------------------------
# 3. _parse_datetime
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def test_none_returns_none(self):
        assert _parse_datetime(None) is None

    def test_blank_string_returns_none(self):
        assert _parse_datetime("") is None
        assert _parse_datetime("   ") is None

    def test_invalid_string_returns_none(self):
        assert _parse_datetime("not-a-date") is None

    def test_iso_string_without_tz_gets_utc(self):
        result = _parse_datetime("2024-06-01T12:00:00")
        assert result is not None
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0

    def test_iso_string_with_z_suffix_parsed(self):
        result = _parse_datetime("2024-06-01T12:00:00Z")
        assert result is not None
        assert result.year == 2024
        assert result.utcoffset().total_seconds() == 0

    def test_iso_string_with_utc_tz(self):
        result = _parse_datetime("2024-06-01T12:00:00+00:00")
        assert result is not None
        assert result.utcoffset().total_seconds() == 0

    def test_iso_string_with_offset_converted_to_utc(self):
        # +05:00 means 12:00 local → 07:00 UTC
        result = _parse_datetime("2024-06-01T12:00:00+05:00")
        assert result is not None
        assert result.utcoffset().total_seconds() == 0
        assert result.hour == 7

    def test_datetime_without_tz_gets_utc(self):
        naive_dt = datetime(2024, 6, 1, 12, 0, 0)
        result = _parse_datetime(naive_dt)
        assert result is not None
        assert result.tzinfo is UTC

    def test_datetime_with_utc_returned_as_utc(self):
        aware_dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        result = _parse_datetime(aware_dt)
        assert result == aware_dt

    def test_datetime_with_offset_converted_to_utc(self):
        plus5 = timezone(timedelta(hours=5))
        aware_dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=plus5)
        result = _parse_datetime(aware_dt)
        assert result is not None
        assert result.utcoffset().total_seconds() == 0
        assert result.hour == 7

    def test_non_string_non_datetime_returns_none(self):
        assert _parse_datetime(42) is None
        assert _parse_datetime([]) is None
        assert _parse_datetime({}) is None


# ---------------------------------------------------------------------------
# 4. _capability_ids
# ---------------------------------------------------------------------------

class TestCapabilityIds:
    def test_non_list_returns_empty_frozenset(self):
        assert _capability_ids(None) == frozenset()
        assert _capability_ids("a,b") == frozenset()
        assert _capability_ids({"a": 1}) == frozenset()

    def test_empty_list_returns_empty_frozenset(self):
        assert _capability_ids([]) == frozenset()

    def test_list_of_strings(self):
        result = _capability_ids(["cap_a", "cap_b"])
        assert result == frozenset({"cap_a", "cap_b"})

    def test_list_of_strings_stripped(self):
        result = _capability_ids(["  cap_a  ", " cap_b "])
        assert result == frozenset({"cap_a", "cap_b"})

    def test_list_of_mappings_with_capability_id(self):
        result = _capability_ids([
            {"capability_id": "cap_x"},
            {"capability_id": "cap_y"},
        ])
        assert result == frozenset({"cap_x", "cap_y"})

    def test_mapping_without_capability_id_ignored(self):
        result = _capability_ids([{"other_key": "value"}])
        assert result == frozenset()

    def test_mixed_strings_and_mappings(self):
        result = _capability_ids(["cap_a", {"capability_id": "cap_b"}])
        assert result == frozenset({"cap_a", "cap_b"})

    def test_empty_strings_ignored(self):
        result = _capability_ids(["", "  ", "cap_a"])
        assert result == frozenset({"cap_a"})

    def test_invalid_items_ignored(self):
        result = _capability_ids([None, 42, [], "valid_cap"])
        assert result == frozenset({"valid_cap"})

    def test_returns_frozenset(self):
        result = _capability_ids(["a"])
        assert isinstance(result, frozenset)

    def test_deduplicated(self):
        result = _capability_ids(["cap_a", "cap_a", {"capability_id": "cap_a"}])
        assert result == frozenset({"cap_a"})
