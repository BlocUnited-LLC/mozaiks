"""
preload_discovery_context.py pure helper unit tests.

Covers:
  _ctx_store:
    - None → {}
    - plain dict → same dict
    - object with .data dict → .data dict
    - other object → returned as-is

  _ctx_get:
    - dict context → value via .get()
    - dict context with missing key → default returned
    - object with .data dict → value accessed
    - callable .get() method on object → used
    - broken .get() → default returned

  _ctx_set:
    - dict context → key set in dict
    - object with .data dict → key set in .data
    - object that supports item assignment → key set

  _coerce_mapping:
    - None → {}
    - plain dict → same dict
    - valid JSON string containing dict → parsed dict
    - invalid JSON string → {}
    - JSON string that isn't a dict → {}
    - non-string, non-dict → {}

  _first_nonempty:
    - all None → None
    - first non-None, non-empty string returned
    - empty string skipped
    - whitespace-only string skipped
    - non-string truthy value returned immediately

  _append_unique:
    - new value → appended
    - duplicate value → not appended
    - None → not appended
    - empty string → not appended

  _normalise_base_url:
    - None → None
    - empty string → None
    - non-string → None
    - trailing slash removed
    - multiple trailing slashes removed
    - no trailing slash → unchanged

  _auth_summary_from_security_schemes:
    - empty dict → "unknown"
    - OAuth2 → "OAuth2"
    - HTTP bearer → "JWT Bearer"
    - apiKey → "API Key"
    - unknown type → type string returned
    - mixed schemes → comma-separated
    - non-dict scheme → skipped

  _infer_stack_from_signals:
    - empty lists → ""
    - deduplicated across languages + frameworks
    - languages + frameworks → comma-separated

  _combine_repo_summaries:
    - no valid summaries → {}
    - single summary → merged result
    - multiple summaries → languages/frameworks deduped

  _merge_unresolved:
    - new question → appended
    - duplicate question → not appended
    - priority defaults to "medium"
    - custom priority used
"""
from __future__ import annotations

from factory_app.workflows.ExistingAppDiscovery.tools.preload_discovery_context import (
    _append_unique,
    _auth_summary_from_security_schemes,
    _coerce_mapping,
    _combine_repo_summaries,
    _ctx_get,
    _ctx_set,
    _ctx_store,
    _first_nonempty,
    _infer_stack_from_signals,
    _merge_unresolved,
    _normalise_base_url,
)

# ---------------------------------------------------------------------------
# 1. _ctx_store
# ---------------------------------------------------------------------------

class TestCtxStore:
    def test_none_returns_empty_dict(self):
        assert _ctx_store(None) == {}

    def test_plain_dict_returned(self):
        d = {"key": "value"}
        result = _ctx_store(d)
        assert result is d

    def test_object_with_data_dict_returns_data(self):
        class Obj:
            data = {"x": 1}
        result = _ctx_store(Obj())
        assert result == {"x": 1}

    def test_object_without_data_returned_as_is(self):
        class Obj:
            pass
        obj = Obj()
        result = _ctx_store(obj)
        assert result is obj

    def test_object_with_non_dict_data_returned_as_is(self):
        class Obj:
            data = "not_a_dict"
        obj = Obj()
        result = _ctx_store(obj)
        assert result is obj


# ---------------------------------------------------------------------------
# 2. _ctx_get
# ---------------------------------------------------------------------------

class TestCtxGet:
    def test_dict_context_returns_value(self):
        assert _ctx_get({"key": "value"}, "key") == "value"

    def test_dict_context_missing_key_returns_default(self):
        assert _ctx_get({"key": "value"}, "missing", "fallback") == "fallback"

    def test_dict_context_missing_key_default_none(self):
        assert _ctx_get({}, "key") is None

    def test_none_context_returns_default(self):
        assert _ctx_get(None, "key", "default") == "default"

    def test_object_with_data_dict(self):
        class Obj:
            data = {"nested": "val"}
        assert _ctx_get(Obj(), "nested") == "val"

    def test_callable_get_method_used(self):
        class Obj:
            def get(self, key, default=None):
                if key == "x":
                    return 42
                return default
        assert _ctx_get(Obj(), "x") == 42
        assert _ctx_get(Obj(), "missing", "fb") == "fb"

    def test_broken_get_returns_default(self):
        class Obj:
            def get(self, *args):
                raise RuntimeError("boom")
        assert _ctx_get(Obj(), "key", "safe") == "safe"


# ---------------------------------------------------------------------------
# 3. _ctx_set
# ---------------------------------------------------------------------------

class TestCtxSet:
    def test_dict_context_sets_key(self):
        d = {"existing": 1}
        _ctx_set(d, "new_key", "new_value")
        assert d["new_key"] == "new_value"

    def test_dict_context_overwrites_existing(self):
        d = {"key": "old"}
        _ctx_set(d, "key", "new")
        assert d["key"] == "new"

    def test_object_with_data_dict_sets_in_data(self):
        class Obj:
            data = {}
        obj = Obj()
        _ctx_set(obj, "k", "v")
        assert obj.data["k"] == "v"

    def test_object_supporting_item_assignment(self):
        class Obj:
            _store = {}
            def __setitem__(self, key, value):
                self._store[key] = value
        obj = Obj()
        _ctx_set(obj, "k", "v")
        assert obj._store["k"] == "v"


# ---------------------------------------------------------------------------
# 4. _coerce_mapping
# ---------------------------------------------------------------------------

class TestCoerceMapping:
    def test_none_returns_empty_dict(self):
        assert _coerce_mapping(None) == {}

    def test_plain_dict_returned(self):
        d = {"key": "value"}
        result = _coerce_mapping(d)
        assert result is d

    def test_valid_json_string_dict_parsed(self):
        import json
        result = _coerce_mapping(json.dumps({"a": 1}))
        assert result == {"a": 1}

    def test_invalid_json_string_returns_empty(self):
        assert _coerce_mapping("{broken") == {}

    def test_json_list_string_returns_empty(self):
        import json
        assert _coerce_mapping(json.dumps(["not", "a", "dict"])) == {}

    def test_json_scalar_string_returns_empty(self):
        import json
        assert _coerce_mapping(json.dumps("just_a_string")) == {}

    def test_non_string_non_dict_returns_empty(self):
        assert _coerce_mapping(42) == {}
        assert _coerce_mapping([1, 2]) == {}
        assert _coerce_mapping(True) == {}


# ---------------------------------------------------------------------------
# 5. _first_nonempty
# ---------------------------------------------------------------------------

class TestFirstNonempty:
    def test_all_none_returns_none(self):
        assert _first_nonempty(None, None, None) is None

    def test_empty_string_skipped(self):
        assert _first_nonempty("", None, "found") == "found"

    def test_whitespace_string_skipped(self):
        assert _first_nonempty("   ", None, "found") == "found"

    def test_first_non_empty_string_returned(self):
        assert _first_nonempty("first", "second") == "first"

    def test_non_string_non_none_returned_immediately(self):
        # 0 is not None and not a string → returned as-is (falsy non-string is not skipped)
        assert _first_nonempty(None, 0, 42) == 0

    def test_int_non_none_returned_before_later_values(self):
        assert _first_nonempty(None, 42) == 42

    def test_no_args_returns_none(self):
        assert _first_nonempty() is None

    def test_false_returned(self):
        # False is not None and not a string → returned
        assert _first_nonempty(None, False) is False


# ---------------------------------------------------------------------------
# 6. _append_unique
# ---------------------------------------------------------------------------

class TestAppendUnique:
    def test_new_value_appended(self):
        items = ["a", "b"]
        _append_unique(items, "c")
        assert items == ["a", "b", "c"]

    def test_duplicate_not_appended(self):
        items = ["a", "b"]
        _append_unique(items, "a")
        assert items == ["a", "b"]

    def test_none_not_appended(self):
        items = ["a"]
        _append_unique(items, None)
        assert items == ["a"]

    def test_empty_string_not_appended(self):
        items = ["a"]
        _append_unique(items, "")
        assert items == ["a"]

    def test_falsy_value_not_appended(self):
        # _append_unique checks `if value` so empty string and None are excluded
        items = []
        _append_unique(items, "")
        _append_unique(items, None)
        assert items == []


# ---------------------------------------------------------------------------
# 7. _normalise_base_url
# ---------------------------------------------------------------------------

class TestNormaliseBaseUrl:
    def test_none_returns_none(self):
        assert _normalise_base_url(None) is None

    def test_empty_string_returns_none(self):
        assert _normalise_base_url("") is None

    def test_non_string_returns_none(self):
        assert _normalise_base_url(123) is None

    def test_trailing_slash_removed(self):
        assert _normalise_base_url("https://example.com/") == "https://example.com"

    def test_multiple_trailing_slashes_removed(self):
        assert _normalise_base_url("https://example.com///") == "https://example.com"

    def test_no_trailing_slash_unchanged(self):
        assert _normalise_base_url("https://example.com") == "https://example.com"

    def test_url_with_path_trailing_slash_removed(self):
        assert _normalise_base_url("https://example.com/api/") == "https://example.com/api"


# ---------------------------------------------------------------------------
# 8. _auth_summary_from_security_schemes
# ---------------------------------------------------------------------------

class TestAuthSummaryFromSecuritySchemes:
    def test_empty_dict_returns_unknown(self):
        assert _auth_summary_from_security_schemes({}) == "unknown"

    def test_none_input_returns_unknown(self):
        assert _auth_summary_from_security_schemes(None) == "unknown"

    def test_oauth2_scheme(self):
        result = _auth_summary_from_security_schemes({"oauth": {"type": "oauth2"}})
        assert result == "OAuth2"

    def test_http_bearer_scheme(self):
        result = _auth_summary_from_security_schemes({"bearer": {"type": "http", "scheme": "bearer"}})
        assert result == "JWT Bearer"

    def test_apikey_scheme(self):
        result = _auth_summary_from_security_schemes({"apiKey": {"type": "apiKey"}})
        assert result == "API Key"

    def test_unknown_type_returned_as_is(self):
        result = _auth_summary_from_security_schemes({"custom": {"type": "custom_auth"}})
        assert result == "custom_auth"

    def test_mixed_schemes_comma_separated(self):
        schemes = {
            "bearerAuth": {"type": "http", "scheme": "bearer"},
            "apiKey": {"type": "apiKey"},
        }
        result = _auth_summary_from_security_schemes(schemes)
        assert "JWT Bearer" in result
        assert "API Key" in result

    def test_non_dict_scheme_skipped(self):
        result = _auth_summary_from_security_schemes({"bad": "not_a_dict"})
        assert result == "unknown"

    def test_scheme_with_no_type_skipped(self):
        result = _auth_summary_from_security_schemes({"s": {}})
        assert result == "unknown"

    def test_deduplication_within_schemes(self):
        # Two oauth2 schemes → only one "OAuth2" in result
        schemes = {
            "s1": {"type": "oauth2"},
            "s2": {"type": "oauth2"},
        }
        result = _auth_summary_from_security_schemes(schemes)
        assert result.count("OAuth2") == 1

    def test_case_insensitive_type_matching(self):
        result = _auth_summary_from_security_schemes({"s": {"type": "OAuth2"}})
        assert result == "OAuth2"


# ---------------------------------------------------------------------------
# 9. _infer_stack_from_signals
# ---------------------------------------------------------------------------

class TestInferStackFromSignals:
    def test_empty_lists_returns_empty_string(self):
        assert _infer_stack_from_signals([], []) == ""

    def test_languages_only(self):
        result = _infer_stack_from_signals(["Python"], [])
        assert result == "Python"

    def test_frameworks_only(self):
        result = _infer_stack_from_signals([], ["FastAPI"])
        assert result == "FastAPI"

    def test_languages_and_frameworks(self):
        result = _infer_stack_from_signals(["Python"], ["FastAPI", "SQLAlchemy"])
        assert "Python" in result
        assert "FastAPI" in result
        assert "SQLAlchemy" in result

    def test_deduplication_across_lists(self):
        result = _infer_stack_from_signals(["Python"], ["Python", "FastAPI"])
        assert result.count("Python") == 1

    def test_order_preserved_languages_first(self):
        result = _infer_stack_from_signals(["Python", "Node.js"], ["React"])
        parts = result.split(", ")
        assert parts[0] == "Python"
        assert parts[1] == "Node.js"
        assert parts[2] == "React"


# ---------------------------------------------------------------------------
# 10. _combine_repo_summaries
# ---------------------------------------------------------------------------

class TestCombineRepoSummaries:
    def test_no_summaries_returns_empty(self):
        assert _combine_repo_summaries() == {}

    def test_failed_summaries_excluded(self):
        assert _combine_repo_summaries({"success": False}) == {}

    def test_single_valid_summary(self):
        result = _combine_repo_summaries({
            "success": True,
            "repo_name": "my-app",
            "languages": ["Python"],
            "frameworks": ["FastAPI"],
            "target_frameworks": [],
            "route_files": ["app/routes.py"],
            "service_entrypoints": ["app/main.py"],
            "hub_files": [],
            "total_files_scanned": 10,
        })
        assert result["success"] is True
        assert "Python" in result["languages"]
        assert "FastAPI" in result["frameworks"]
        assert result["total_files_scanned"] == 10

    def test_multiple_summaries_merged(self):
        s1 = {
            "success": True,
            "repo_name": "app1",
            "languages": ["Python"],
            "frameworks": ["FastAPI"],
            "target_frameworks": [],
            "route_files": ["app1/routes.py"],
            "service_entrypoints": [],
            "hub_files": [],
            "total_files_scanned": 5,
        }
        s2 = {
            "success": True,
            "repo_name": "app2",
            "languages": ["Node.js"],
            "frameworks": ["React"],
            "target_frameworks": [],
            "route_files": ["app2/routes.js"],
            "service_entrypoints": [],
            "hub_files": [],
            "total_files_scanned": 8,
        }
        result = _combine_repo_summaries(s1, s2)
        assert "Python" in result["languages"]
        assert "Node.js" in result["languages"]
        assert result["total_files_scanned"] == 13
        assert result["source"] == "multi_repo"

    def test_deduplicated_languages(self):
        s1 = {"success": True, "languages": ["Python"], "frameworks": [], "target_frameworks": [], "route_files": [], "service_entrypoints": [], "hub_files": [], "total_files_scanned": 0}
        s2 = {"success": True, "languages": ["Python"], "frameworks": [], "target_frameworks": [], "route_files": [], "service_entrypoints": [], "hub_files": [], "total_files_scanned": 0}
        result = _combine_repo_summaries(s1, s2)
        assert result["languages"].count("Python") == 1

    def test_missing_optional_fields_handled(self):
        result = _combine_repo_summaries({"success": True})
        assert result["languages"] == []
        assert result["frameworks"] == []

    def test_inferred_tech_stack_present(self):
        result = _combine_repo_summaries({
            "success": True,
            "languages": ["Python"],
            "frameworks": ["FastAPI"],
            "target_frameworks": [],
            "route_files": [],
            "service_entrypoints": [],
            "hub_files": [],
            "total_files_scanned": 0,
        })
        assert "inferred_tech_stack" in result
        assert "Python" in result["inferred_tech_stack"]


# ---------------------------------------------------------------------------
# 11. _merge_unresolved
# ---------------------------------------------------------------------------

class TestMergeUnresolved:
    def test_new_question_appended(self):
        items = []
        _merge_unresolved(items, "What is the auth strategy?", "No auth detected")
        assert len(items) == 1
        assert items[0]["question"] == "What is the auth strategy?"

    def test_duplicate_question_not_appended(self):
        items = [{"question": "What is auth?", "context": "x", "priority": "medium"}]
        _merge_unresolved(items, "What is auth?", "different context")
        assert len(items) == 1

    def test_default_priority_is_medium(self):
        items = []
        _merge_unresolved(items, "How is data stored?", "No DB detected")
        assert items[0]["priority"] == "medium"

    def test_custom_priority_used(self):
        items = []
        _merge_unresolved(items, "Urgent question?", "context", priority="high")
        assert items[0]["priority"] == "high"

    def test_context_stored(self):
        items = []
        _merge_unresolved(items, "What framework?", "Detected Node.js")
        assert items[0]["context"] == "Detected Node.js"

    def test_multiple_unique_questions(self):
        items = []
        _merge_unresolved(items, "Q1?", "ctx1")
        _merge_unresolved(items, "Q2?", "ctx2")
        assert len(items) == 2

    def test_modifies_list_in_place(self):
        items = []
        result = _merge_unresolved(items, "Q?", "ctx")
        assert result is None  # returns None
        assert len(items) == 1
