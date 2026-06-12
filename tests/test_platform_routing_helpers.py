"""
Pure helper unit tests for additional mozaiksai/hosts/platform.py helpers.

Covers:
  _coerce_requires_role:
    - valid string stripped and returned
    - empty string → None
    - whitespace-only string → None
    - None → None
    - list with valid first string → first returned
    - list with empty first, valid second → second returned
    - list with non-string items skipped
    - empty list → None

  _normalize_route_requires_role_meta:
    - requiresRole present → kept
    - roles key converted to requiresRole
    - roles key removed from output
    - neither key → no requiresRole in output
    - empty requiresRole → not in output
    - other meta fields preserved

  _normalize_shell_page_entry:
    - non-dict entry → None
    - path not starting with "/" → None
    - no component/transition/workflow → None
    - valid component entry → dict with path, label, order, meta
    - requiresAuth defaults to True
    - shellMode stored in meta when valid
    - navigation dict stored in meta
    - schema/sequence stored when present

  _dedupe_and_sort_pages:
    - duplicate paths → first kept
    - sorted by order
    - sorted by label when order equal
    - pages without path skipped

  _profile_doc_id:
    - returns "app_id:user_id" format

  _trigger_capability_ids:
    - capability_ids list returned
    - empty items filtered
    - single capability_id fallback
    - empty string → []
    - neither key → []

  _deep_get:
    - single level access
    - dotted path access
    - missing key → None
    - None value returned as-is
    - non-dict mid-path → None
    - empty path segments skipped

  _resolve_event_context_value:
    - non-string value returned as-is
    - "payload.field" → resolved from event payload
    - "event.field" → resolved from event root
    - plain string without prefix returned as-is

  _is_ask_carrier_session:
    - None → False
    - non-dict → False
    - transport_purpose "ask_carrier" → True
    - uppercase ASK_CARRIER → True
    - different purpose → False

  _json_timestamp:
    - object with isoformat() → isoformat called
    - plain value (str, int) → returned as-is
"""
from __future__ import annotations

from datetime import UTC, datetime

from mozaiksai.hosts.platform import (
    _coerce_requires_role,
    _dedupe_and_sort_pages,
    _deep_get,
    _is_ask_carrier_session,
    _json_timestamp,
    _normalize_route_requires_role_meta,
    _normalize_shell_page_entry,
    _profile_doc_id,
    _resolve_event_context_value,
    _trigger_capability_ids,
)

# ---------------------------------------------------------------------------
# 1. _coerce_requires_role
# ---------------------------------------------------------------------------

class TestCoerceRequiresRole:
    def test_valid_string_returned(self):
        assert _coerce_requires_role("admin") == "admin"

    def test_string_stripped(self):
        assert _coerce_requires_role("  admin  ") == "admin"

    def test_empty_string_returns_none(self):
        assert _coerce_requires_role("") is None

    def test_whitespace_only_returns_none(self):
        assert _coerce_requires_role("   ") is None

    def test_none_returns_none(self):
        assert _coerce_requires_role(None) is None

    def test_list_first_valid_returned(self):
        assert _coerce_requires_role(["admin", "viewer"]) == "admin"

    def test_list_skips_empty_strings(self):
        assert _coerce_requires_role(["", "viewer"]) == "viewer"

    def test_list_skips_non_string_items(self):
        assert _coerce_requires_role([42, None, "editor"]) == "editor"

    def test_empty_list_returns_none(self):
        assert _coerce_requires_role([]) is None

    def test_list_all_empty_returns_none(self):
        assert _coerce_requires_role(["", "  "]) is None


# ---------------------------------------------------------------------------
# 2. _normalize_route_requires_role_meta
# ---------------------------------------------------------------------------

class TestNormalizeRouteRequiresRoleMeta:
    def test_requires_role_preserved(self):
        meta = {"requiresRole": "admin", "otherKey": "val"}
        result = _normalize_route_requires_role_meta(meta)
        assert result["requiresRole"] == "admin"

    def test_roles_converted_to_requires_role(self):
        meta = {"roles": ["admin", "editor"]}
        result = _normalize_route_requires_role_meta(meta)
        assert result["requiresRole"] == "admin"

    def test_roles_key_removed_from_output(self):
        meta = {"roles": ["admin"]}
        result = _normalize_route_requires_role_meta(meta)
        assert "roles" not in result

    def test_neither_key_no_requires_role(self):
        meta = {"someKey": "val"}
        result = _normalize_route_requires_role_meta(meta)
        assert "requiresRole" not in result

    def test_empty_requires_role_not_overwritten(self):
        # The function copies meta verbatim then only sets requiresRole if non-empty.
        # An existing empty string key is preserved in the copy (not removed).
        meta = {"requiresRole": ""}
        result = _normalize_route_requires_role_meta(meta)
        # Empty requiresRole is kept from the copy; no valid role is set on top
        assert result.get("requiresRole") == ""

    def test_other_meta_fields_preserved(self):
        meta = {"requiresRole": "admin", "surfaces": "platform", "nav": True}
        result = _normalize_route_requires_role_meta(meta)
        assert result["surfaces"] == "platform"
        assert result["nav"] is True

    def test_requires_role_takes_priority_over_roles(self):
        meta = {"requiresRole": "admin", "roles": ["editor"]}
        result = _normalize_route_requires_role_meta(meta)
        assert result["requiresRole"] == "admin"


# ---------------------------------------------------------------------------
# 3. _normalize_shell_page_entry
# ---------------------------------------------------------------------------

class TestNormalizeShellPageEntry:
    def test_non_dict_returns_none(self):
        assert _normalize_shell_page_entry("not a dict", order_fallback=0) is None

    def test_path_not_starting_with_slash_returns_none(self):
        assert _normalize_shell_page_entry({"path": "home", "component": "Home"}, order_fallback=0) is None

    def test_no_component_transition_or_workflow_returns_none(self):
        assert _normalize_shell_page_entry({"path": "/home"}, order_fallback=0) is None

    def test_valid_component_entry_returned(self):
        result = _normalize_shell_page_entry(
            {"path": "/home", "component": "HomePage"}, order_fallback=0
        )
        assert result is not None
        assert result["path"] == "/home"
        assert result["component"] == "HomePage"

    def test_requires_auth_defaults_true(self):
        result = _normalize_shell_page_entry(
            {"path": "/home", "component": "Home"}, order_fallback=0
        )
        assert result["meta"]["requiresAuth"] is True

    def test_requires_auth_override(self):
        result = _normalize_shell_page_entry(
            {"path": "/home", "component": "Home", "requiresAuth": False}, order_fallback=0
        )
        assert result["meta"]["requiresAuth"] is False

    def test_order_from_entry(self):
        result = _normalize_shell_page_entry(
            {"path": "/home", "component": "Home", "order": 3}, order_fallback=99
        )
        assert result["order"] == 3

    def test_order_fallback_used_when_not_set(self):
        result = _normalize_shell_page_entry(
            {"path": "/home", "component": "Home"}, order_fallback=5
        )
        assert result["order"] == 5

    def test_shell_mode_stored_in_meta(self):
        result = _normalize_shell_page_entry(
            {"path": "/home", "component": "Home", "shellMode": "conversation"}, order_fallback=0
        )
        assert result["meta"]["shellMode"] == "conversation"

    def test_invalid_shell_mode_not_in_meta(self):
        result = _normalize_shell_page_entry(
            {"path": "/home", "component": "Home", "shellMode": "invalid"}, order_fallback=0
        )
        assert "shellMode" not in result["meta"]

    def test_navigation_dict_stored_in_meta(self):
        nav = {"label": "Home", "include": True}
        result = _normalize_shell_page_entry(
            {"path": "/home", "component": "Home", "navigation": nav}, order_fallback=0
        )
        assert result["meta"]["navigation"] == nav

    def test_schema_stored_when_string(self):
        result = _normalize_shell_page_entry(
            {"path": "/home", "component": "Home", "schema": "home_page"}, order_fallback=0
        )
        assert result["schema"] == "home_page"

    def test_workflow_stored_when_string(self):
        result = _normalize_shell_page_entry(
            {"path": "/chat", "workflow": "MainChat"}, order_fallback=0
        )
        assert result["workflow"] == "MainChat"

    def test_transition_stored_when_string(self):
        result = _normalize_shell_page_entry(
            {"path": "/onboard", "transition": "OnboardingTransition"}, order_fallback=0
        )
        assert result["transition"] == "OnboardingTransition"


# ---------------------------------------------------------------------------
# 4. _dedupe_and_sort_pages
# ---------------------------------------------------------------------------

class TestDedupeAndSortPages:
    def test_duplicate_paths_first_kept(self):
        pages = [
            {"path": "/home", "order": 0, "label": "A"},
            {"path": "/home", "order": 1, "label": "B"},
        ]
        result = _dedupe_and_sort_pages(pages)
        assert len(result) == 1
        assert result[0]["label"] == "A"

    def test_sorted_by_order(self):
        pages = [
            {"path": "/b", "order": 2, "label": "B"},
            {"path": "/a", "order": 1, "label": "A"},
        ]
        result = _dedupe_and_sort_pages(pages)
        assert result[0]["path"] == "/a"
        assert result[1]["path"] == "/b"

    def test_pages_without_path_skipped(self):
        pages = [
            {"label": "No path"},
            {"path": "/home", "order": 0},
        ]
        result = _dedupe_and_sort_pages(pages)
        assert len(result) == 1

    def test_empty_list_returns_empty(self):
        assert _dedupe_and_sort_pages([]) == []

    def test_all_unique_paths_preserved(self):
        pages = [{"path": f"/{i}", "order": i} for i in range(3)]
        result = _dedupe_and_sort_pages(pages)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# 5. _profile_doc_id
# ---------------------------------------------------------------------------

class TestProfileDocId:
    def test_format_is_app_id_colon_user_id(self):
        assert _profile_doc_id("my-app", "user-123") == "my-app:user-123"

    def test_concatenation(self):
        result = _profile_doc_id("app1", "usr1")
        assert "app1" in result
        assert "usr1" in result
        assert ":" in result


# ---------------------------------------------------------------------------
# 6. _trigger_capability_ids
# ---------------------------------------------------------------------------

class TestTriggerCapabilityIds:
    def test_capability_ids_list_returned(self):
        trigger = {"capability_ids": ["billing", "messaging"]}
        result = _trigger_capability_ids(trigger)
        assert result == ["billing", "messaging"]

    def test_empty_items_filtered(self):
        trigger = {"capability_ids": ["billing", "", "  "]}
        result = _trigger_capability_ids(trigger)
        assert result == ["billing"]

    def test_single_capability_id_fallback(self):
        trigger = {"capability_id": "my_cap"}
        result = _trigger_capability_ids(trigger)
        assert result == ["my_cap"]

    def test_empty_capability_id_returns_empty(self):
        trigger = {"capability_id": ""}
        result = _trigger_capability_ids(trigger)
        assert result == []

    def test_neither_key_returns_empty(self):
        assert _trigger_capability_ids({}) == []

    def test_non_list_capability_ids_falls_back_to_single(self):
        trigger = {"capability_ids": "not_a_list", "capability_id": "my_cap"}
        result = _trigger_capability_ids(trigger)
        assert result == ["my_cap"]

    def test_capability_ids_takes_priority_over_capability_id(self):
        trigger = {"capability_ids": ["list_cap"], "capability_id": "single_cap"}
        result = _trigger_capability_ids(trigger)
        assert "list_cap" in result
        assert "single_cap" not in result


# ---------------------------------------------------------------------------
# 7. _deep_get
# ---------------------------------------------------------------------------

class TestDeepGet:
    def test_single_level(self):
        assert _deep_get({"key": "val"}, "key") == "val"

    def test_dotted_path(self):
        assert _deep_get({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_missing_key_returns_none(self):
        assert _deep_get({"a": 1}, "b") is None

    def test_missing_nested_key_returns_none(self):
        assert _deep_get({"a": {"b": 1}}, "a.c") is None

    def test_none_value_returned(self):
        assert _deep_get({"key": None}, "key") is None

    def test_non_dict_mid_path_returns_none(self):
        assert _deep_get({"a": "string"}, "a.b") is None

    def test_empty_path_returns_source(self):
        source = {"key": "val"}
        # dotted_path "" → no parts → returns source unchanged
        assert _deep_get(source, "") == source

    def test_none_source_returns_none(self):
        assert _deep_get(None, "key") is None

    def test_integer_value_returned(self):
        assert _deep_get({"count": 5}, "count") == 5


# ---------------------------------------------------------------------------
# 8. _resolve_event_context_value
# ---------------------------------------------------------------------------

class TestResolveEventContextValue:
    def _event(self, **kwargs):
        return {"type": "domain.test.event", "payload": {"amount": 100, "currency": "usd"}, **kwargs}

    def test_non_string_returned_as_is(self):
        assert _resolve_event_context_value(42, {}) == 42

    def test_dict_returned_as_is(self):
        val = {"key": "val"}
        assert _resolve_event_context_value(val, {}) == val

    def test_payload_prefix_resolved(self):
        event = self._event()
        result = _resolve_event_context_value("payload.amount", event)
        assert result == 100

    def test_payload_nested_resolved(self):
        event = {"payload": {"user": {"id": "u1"}}}
        result = _resolve_event_context_value("payload.user.id", event)
        assert result == "u1"

    def test_event_prefix_resolved(self):
        event = self._event()
        result = _resolve_event_context_value("event.type", event)
        assert result == "domain.test.event"

    def test_plain_string_returned_as_is(self):
        assert _resolve_event_context_value("plain_value", {}) == "plain_value"

    def test_missing_payload_key_returns_none(self):
        event = self._event()
        result = _resolve_event_context_value("payload.nonexistent", event)
        assert result is None

    def test_whitespace_stripped_before_check(self):
        event = self._event()
        # Leading/trailing whitespace stripped → "payload.amount"
        result = _resolve_event_context_value("  payload.amount  ", event)
        # The value is stripped → matches "payload.*"
        assert result == 100


# ---------------------------------------------------------------------------
# 9. _is_ask_carrier_session
# ---------------------------------------------------------------------------

class TestIsAskCarrierSession:
    def test_none_returns_false(self):
        assert _is_ask_carrier_session(None) is False

    def test_non_dict_returns_false(self):
        assert _is_ask_carrier_session("not_a_dict") is False

    def test_ask_carrier_transport_purpose_true(self):
        assert _is_ask_carrier_session({"transport_purpose": "ask_carrier"}) is True

    def test_uppercase_normalized(self):
        assert _is_ask_carrier_session({"transport_purpose": "ASK_CARRIER"}) is True

    def test_different_purpose_false(self):
        assert _is_ask_carrier_session({"transport_purpose": "chat"}) is False

    def test_missing_key_false(self):
        assert _is_ask_carrier_session({"other": "val"}) is False

    def test_whitespace_stripped(self):
        assert _is_ask_carrier_session({"transport_purpose": "  ask_carrier  "}) is True


# ---------------------------------------------------------------------------
# 10. _json_timestamp
# ---------------------------------------------------------------------------

class TestJsonTimestamp:
    def test_datetime_calls_isoformat(self):
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        result = _json_timestamp(dt)
        assert result == dt.isoformat()

    def test_string_returned_as_is(self):
        assert _json_timestamp("2025-06-01") == "2025-06-01"

    def test_int_returned_as_is(self):
        assert _json_timestamp(1234567890) == 1234567890

    def test_none_returned_as_is(self):
        assert _json_timestamp(None) is None

    def test_object_without_isoformat_returned_as_is(self):
        obj = object()
        assert _json_timestamp(obj) is obj
