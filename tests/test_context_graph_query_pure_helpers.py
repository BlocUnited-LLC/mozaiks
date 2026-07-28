"""
Pure helper unit tests for mozaiksai/control_plane/context_graph/query.py
and mozaiksai/core/app_context/health.py.

Covers (query.py):
  _contract_role_boost:
    - "module_action_handler" → 45
    - "module_service_symbol" → 35
    - "module_repo_symbol" → 35
    - "page_component" → 25
    - "ui_component" → 25
    - "module_contract" → 20
    - "workflow_agent" → 20
    - "workflow_tool" → 20
    - empty/unknown → 0

  _path_priority_score:
    - "tests" label, wants_tests=False → -35
    - "tests" label, wants_tests=True → positive
    - "docs" label, wants_docs=False → -25
    - "docs" label, wants_docs=True → positive
    - "scripts" label → -20
    - priority <= 12 → 40 - priority
    - priority 13-20 → 18
    - priority 21-49 → 8
    - priority >= 50 → 0

  _keywords:
    - stopwords excluded ("the", "and", etc.)
    - tokens shorter than 3 chars excluded
    - deduplicates
    - capped at 16
    - None/empty → []
    - lowercase output

  _normalize_path (query):
    - empty string → None
    - None → None
    - backslashes → forward slashes
    - leading/trailing slashes stripped
    - clean path returned

  _dedupe (query):
    - duplicates removed, order preserved
    - None values excluded
    - empty strings excluded

  _excerpt:
    - None → None
    - short content returned as-is
    - long content truncated with "... [truncated]"
    - max_length respected

Covers (health.py):
  _int:
    - int → int
    - float → int (truncated)
    - string int → int
    - invalid string → 0
    - None → 0

  _dict_ints:
    - non-dict → {}
    - valid dict → string keys, int values
    - empty keys excluded
    - values coerced via _int

  _parser_fallback_warning:
    - more js-like files than python → check parser
    - js "regex" active_parser → warning
    - js "tree-sitter" active_parser → None
    - more python than js → None
    - equal counts → None
"""
from __future__ import annotations

from mozaiksai.control_plane.context_graph.query import (
    _contract_role_boost,
    _dedupe,
    _excerpt,
    _keywords,
    _normalize_path,
    _path_priority_score,
)
from mozaiksai.core.app_context.health import (
    _dict_ints,
    _int,
    _parser_fallback_warning,
)

# ---------------------------------------------------------------------------
# 1. _contract_role_boost
# ---------------------------------------------------------------------------


class TestContractRoleBoost:
    def _node(self, role: str | None):
        class FakeNode:
            metadata = {"contract_role": role}

        return FakeNode()

    def test_module_action_handler(self):
        assert _contract_role_boost(self._node("module_action_handler")) == 45

    def test_module_service_symbol(self):
        assert _contract_role_boost(self._node("module_service_symbol")) == 35

    def test_module_repo_symbol(self):
        assert _contract_role_boost(self._node("module_repo_symbol")) == 35

    def test_page_component(self):
        assert _contract_role_boost(self._node("page_component")) == 25

    def test_ui_component(self):
        assert _contract_role_boost(self._node("ui_component")) == 25

    def test_module_contract(self):
        assert _contract_role_boost(self._node("module_contract")) == 20

    def test_workflow_agent(self):
        assert _contract_role_boost(self._node("workflow_agent")) == 20

    def test_workflow_tool(self):
        assert _contract_role_boost(self._node("workflow_tool")) == 20

    def test_unknown_role_zero(self):
        assert _contract_role_boost(self._node("unknown_role")) == 0

    def test_empty_role_zero(self):
        assert _contract_role_boost(self._node("")) == 0

    def test_none_role_zero(self):
        assert _contract_role_boost(self._node(None)) == 0


# ---------------------------------------------------------------------------
# 2. _path_priority_score
# ---------------------------------------------------------------------------

class TestPathPriorityScore:
    def test_tests_label_no_wants_tests(self):
        assert _path_priority_score(1, "tests", wants_tests=False, wants_docs=False) == -35

    def test_tests_label_wants_tests(self):
        result = _path_priority_score(1, "tests", wants_tests=True, wants_docs=False)
        assert result > 0

    def test_docs_label_no_wants_docs(self):
        assert _path_priority_score(1, "docs", wants_tests=False, wants_docs=False) == -25

    def test_docs_label_wants_docs(self):
        result = _path_priority_score(1, "docs", wants_tests=False, wants_docs=True)
        assert result > 0

    def test_scripts_label(self):
        assert _path_priority_score(1, "scripts", wants_tests=False, wants_docs=False) == -20

    def test_priority_zero_gives_40(self):
        assert _path_priority_score(0, "other", wants_tests=False, wants_docs=False) == 40

    def test_priority_12_gives_28(self):
        assert _path_priority_score(12, "other", wants_tests=False, wants_docs=False) == 28

    def test_priority_13_gives_18(self):
        assert _path_priority_score(13, "other", wants_tests=False, wants_docs=False) == 18

    def test_priority_20_gives_18(self):
        assert _path_priority_score(20, "other", wants_tests=False, wants_docs=False) == 18

    def test_priority_21_gives_8(self):
        assert _path_priority_score(21, "other", wants_tests=False, wants_docs=False) == 8

    def test_priority_49_gives_8(self):
        assert _path_priority_score(49, "other", wants_tests=False, wants_docs=False) == 8

    def test_priority_50_gives_0(self):
        assert _path_priority_score(50, "other", wants_tests=False, wants_docs=False) == 0

    def test_priority_100_gives_0(self):
        assert _path_priority_score(100, "other", wants_tests=False, wants_docs=False) == 0


# ---------------------------------------------------------------------------
# 3. _keywords
# ---------------------------------------------------------------------------

class TestKeywords:
    def test_stopwords_excluded(self):
        result = _keywords("add the billing module")
        assert "the" not in result
        assert "add" not in result
        assert "billing" in result
        assert "module" in result  # "module" is NOT a stopword

    def test_short_tokens_excluded(self):
        result = _keywords("ab xy abc")
        assert "ab" not in result
        assert "xy" not in result
        assert "abc" in result

    def test_deduplicates(self):
        result = _keywords("billing billing billing")
        assert result.count("billing") == 1

    def test_capped_at_16(self):
        long_request = " ".join(f"word{i}x" for i in range(30))
        result = _keywords(long_request)
        assert len(result) <= 16

    def test_none_returns_empty(self):
        assert _keywords(None) == []  # type: ignore[arg-type]

    def test_empty_string_returns_empty(self):
        assert _keywords("") == []

    def test_lowercase_output(self):
        result = _keywords("BILLING MODULE")
        for token in result:
            assert token == token.lower()

    def test_preserves_order(self):
        result = _keywords("search payment gateway api")
        assert result.index("search") < result.index("payment")


# ---------------------------------------------------------------------------
# 4. _normalize_path (query module)
# ---------------------------------------------------------------------------

class TestNormalizePathQuery:
    def test_empty_returns_none(self):
        assert _normalize_path("") is None

    def test_none_returns_none(self):
        assert _normalize_path(None) is None  # type: ignore[arg-type]

    def test_backslashes_normalized(self):
        result = _normalize_path("modules\\billing\\handler.py")
        assert result == "modules/billing/handler.py"

    def test_leading_slash_stripped(self):
        result = _normalize_path("/modules/billing/handler.py")
        assert result == "modules/billing/handler.py"

    def test_trailing_slash_stripped(self):
        result = _normalize_path("modules/billing/")
        assert result == "modules/billing"

    def test_clean_path_returned(self):
        result = _normalize_path("modules/billing/handler.py")
        assert result == "modules/billing/handler.py"

    def test_whitespace_stripped(self):
        result = _normalize_path("  modules/billing  ")
        assert result == "modules/billing"


# ---------------------------------------------------------------------------
# 5. _dedupe (query module)
# ---------------------------------------------------------------------------

class TestDedupeQuery:
    def test_duplicates_removed(self):
        result = _dedupe(["a", "b", "a"])
        assert result.count("a") == 1

    def test_order_preserved(self):
        result = _dedupe(["c", "a", "b"])
        assert result == ["c", "a", "b"]

    def test_none_excluded(self):
        result = _dedupe(["a", None, "b"])
        assert None not in result

    def test_empty_string_excluded(self):
        result = _dedupe(["a", "", "b"])
        assert "" not in result

    def test_empty_list_returns_empty(self):
        assert _dedupe([]) == []


# ---------------------------------------------------------------------------
# 6. _excerpt
# ---------------------------------------------------------------------------

class TestExcerpt:
    def test_none_returns_none(self):
        assert _excerpt(None, max_length=100) is None

    def test_short_content_returned_as_is(self):
        assert _excerpt("hello", max_length=100) == "hello"

    def test_exact_max_length_returned_as_is(self):
        content = "a" * 100
        assert _excerpt(content, max_length=100) == content

    def test_long_content_truncated(self):
        content = "a" * 200
        result = _excerpt(content, max_length=100)
        assert "... [truncated]" in result

    def test_truncated_result_respects_max_length(self):
        content = "x" * 500
        result = _excerpt(content, max_length=50)
        assert result is not None
        assert "truncated" in result

    def test_empty_string_returned_as_is(self):
        assert _excerpt("", max_length=10) == ""


# ---------------------------------------------------------------------------
# 7. _int (health module)
# ---------------------------------------------------------------------------

class TestIntHealth:
    def test_int_passthrough(self):
        assert _int(5) == 5

    def test_float_truncated(self):
        assert _int(3.9) == 3

    def test_string_int(self):
        assert _int("42") == 42

    def test_invalid_string_returns_zero(self):
        assert _int("abc") == 0

    def test_none_returns_zero(self):
        assert _int(None) == 0

    def test_empty_string_returns_zero(self):
        assert _int("") == 0

    def test_negative_int(self):
        assert _int(-5) == -5


# ---------------------------------------------------------------------------
# 8. _dict_ints (health module)
# ---------------------------------------------------------------------------

class TestDictInts:
    def test_non_dict_returns_empty(self):
        assert _dict_ints("not_a_dict") == {}

    def test_none_returns_empty(self):
        assert _dict_ints(None) == {}

    def test_list_returns_empty(self):
        assert _dict_ints([1, 2, 3]) == {}

    def test_valid_dict_coerced(self):
        result = _dict_ints({".py": 10, ".js": 5})
        assert result[".py"] == 10
        assert result[".js"] == 5

    def test_string_values_coerced_to_int(self):
        result = _dict_ints({".py": "20"})
        assert result[".py"] == 20

    def test_empty_keys_excluded(self):
        result = _dict_ints({"": 5, ".py": 3})
        assert "" not in result
        assert result[".py"] == 3

    def test_invalid_value_becomes_zero(self):
        result = _dict_ints({".py": "abc"})
        assert result[".py"] == 0

    def test_empty_dict_returns_empty(self):
        assert _dict_ints({}) == {}


# ---------------------------------------------------------------------------
# 9. _parser_fallback_warning (health module)
# ---------------------------------------------------------------------------

class TestParserFallbackWarning:
    def _status(self, active_parser: str) -> dict:
        return {
            "languages": {
                "javascript": {"active_parser": active_parser}
            }
        }

    def test_more_js_regex_parser_returns_warning(self):
        result = _parser_fallback_warning(
            parser_status=self._status("regex"),
            selected_by_extension={".js": 10, ".py": 2},
        )
        assert result == "context_graph_javascript_parser_fallback"

    def test_more_js_tree_sitter_returns_none(self):
        result = _parser_fallback_warning(
            parser_status=self._status("tree-sitter"),
            selected_by_extension={".js": 10, ".py": 2},
        )
        assert result is None

    def test_more_python_than_js_returns_none(self):
        result = _parser_fallback_warning(
            parser_status=self._status("regex"),
            selected_by_extension={".py": 20, ".js": 5},
        )
        assert result is None

    def test_equal_counts_returns_none(self):
        result = _parser_fallback_warning(
            parser_status=self._status("regex"),
            selected_by_extension={".js": 5, ".py": 5},
        )
        assert result is None

    def test_tsx_counted_as_js_like(self):
        result = _parser_fallback_warning(
            parser_status=self._status("regex"),
            selected_by_extension={".tsx": 10, ".py": 2},
        )
        assert result == "context_graph_javascript_parser_fallback"

    def test_no_js_files_returns_none(self):
        result = _parser_fallback_warning(
            parser_status=self._status("regex"),
            selected_by_extension={".py": 5},
        )
        assert result is None
