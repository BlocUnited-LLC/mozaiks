"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/module_runtime_quality.py

Covers:
  _dedupe:
    - empty iterable → []
    - no duplicates → unchanged
    - duplicates removed preserving order
    - works with generator input
    - single item

  _safe_path:
    - non-string → ""
    - absolute path → ""
    - path with ".." traversal → ""
    - embedded ".." segment → ""
    - backslashes normalised to forward slashes
    - leading/trailing whitespace stripped
    - normal relative path → returned as-is
    - None → ""

  _is_summary_function:
    - FunctionDef whose name contains "summary" → True
    - FunctionDef whose name contains "stats" → True
    - FunctionDef whose name contains "metrics" → True
    - FunctionDef whose name contains "metric" → True
    - FunctionDef whose name contains "count" → True
    - FunctionDef whose name contains "dashboard" → True
    - FunctionDef whose name contains "overview" → True
    - AsyncFunctionDef matching pattern → True
    - FunctionDef with unrelated name → False
    - Non-function AST node → False
    - Case-insensitive match → True

  _has_data_dependency:
    - AST Name node matching _DATA_ACCESS_NAMES ("repo") → True
    - AST Name node matching "db" → True
    - AST Attribute node matching _DATA_ACCESS_ATTRS ("find") → True
    - AST Attribute matching "aggregate" → True
    - AST Attribute matching "count_documents" → True
    - No data access names → False
    - Pure arithmetic return → False
    - Nested access found recursively → True

  _literal_metric_value (ast.Constant cases):
    - int literal → True
    - float literal → True
    - None → False
    - True (bool) → False
    - False (bool) → False
    - percent string "50%" → True
    - string containing "change" → True
    - string containing "growth" → True
    - string containing "trend" → True
    - string containing "demo" → True
    - string containing "sample" → True
    - plain string with no tokens → False
    - plain string with trend_context=True → True
    - empty string → False

  _literal_metric_value (container cases):
    - list with int → True
    - list with only safe strings → False
    - dict with trend key + int value → True
    - dict with non-trend key + string (no trend_context) → False
    - dict with trend key + None value → False (None is not literal metric)

  _static_trend_value:
    - non-dict node → False
    - dict with no trend keys → False
    - dict with trend key + None value → False (honest empty)
    - dict with trend key + int value → True
    - dict with trend key + string value (trend_context→True) → True
    - dict with non-trend key only → False
    - dict mixing trend+None and trend+int → True
"""
from __future__ import annotations

import ast
from collections.abc import Iterator

from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
    _dedupe,
    _has_data_dependency,
    _is_summary_function,
    _literal_metric_value,
    _safe_path,
    _static_trend_value,
)

# ---------------------------------------------------------------------------
# Helpers for building AST nodes inline
# ---------------------------------------------------------------------------

def _parse_expr(src: str) -> ast.expr:
    """Parse a single expression and return the AST expression node."""
    return ast.parse(src, mode="eval").body  # type: ignore[return-value]


def _parse_stmt(src: str) -> ast.stmt:
    """Parse source code and return the first statement node."""
    return ast.parse(src).body[0]  # type: ignore[return-value]


def _const(value: object) -> ast.Constant:
    """Return an ast.Constant node for a Python literal value."""
    return _parse_expr(repr(value))  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_returns_empty(self):
        assert _dedupe([]) == []

    def test_no_duplicates_unchanged(self):
        result = _dedupe(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_duplicates_removed(self):
        result = _dedupe(["a", "b", "a", "c", "b"])
        assert result == ["a", "b", "c"]

    def test_order_preserved(self):
        result = _dedupe(["z", "a", "z", "m"])
        assert result == ["z", "a", "m"]

    def test_single_item(self):
        assert _dedupe(["only"]) == ["only"]

    def test_all_same(self):
        result = _dedupe(["x", "x", "x"])
        assert result == ["x"]

    def test_generator_input(self):
        def _gen() -> Iterator[str]:
            yield "a"
            yield "b"
            yield "a"

        assert _dedupe(_gen()) == ["a", "b"]

    def test_empty_string_items_deduplicated(self):
        result = _dedupe(["", "a", ""])
        assert result == ["", "a"]


# ---------------------------------------------------------------------------
# 2. _safe_path
# ---------------------------------------------------------------------------

class TestSafePath:
    def test_none_returns_empty(self):
        assert _safe_path(None) == ""

    def test_integer_returns_empty(self):
        assert _safe_path(42) == ""

    def test_list_returns_empty(self):
        assert _safe_path(["modules/billing/handler.py"]) == ""

    def test_absolute_path_rejected(self):
        assert _safe_path("/absolute/path/handler.py") == ""

    def test_traversal_at_start_rejected(self):
        assert _safe_path("../escape") == ""

    def test_embedded_traversal_rejected(self):
        assert _safe_path("modules/billing/../other/handler.py") == ""

    def test_backslashes_normalised(self):
        result = _safe_path("modules\\billing\\handler.py")
        assert result == "modules/billing/handler.py"

    def test_leading_trailing_whitespace_stripped(self):
        result = _safe_path("  modules/billing/handler.py  ")
        assert result == "modules/billing/handler.py"

    def test_normal_relative_path_returned(self):
        assert _safe_path("modules/billing/handler.py") == "modules/billing/handler.py"

    def test_single_filename(self):
        assert _safe_path("handler.py") == "handler.py"

    def test_mixed_backslash_forward(self):
        result = _safe_path("modules\\billing/handler.py")
        assert result == "modules/billing/handler.py"

    def test_empty_string_returns_dot(self):
        # PurePosixPath("") evaluates to "."
        assert _safe_path("") == "."


# ---------------------------------------------------------------------------
# 3. _is_summary_function
# ---------------------------------------------------------------------------

class TestIsSummaryFunction:
    def test_summary_in_name(self):
        node = _parse_stmt("def get_summary(): pass")
        assert _is_summary_function(node) is True

    def test_stats_in_name(self):
        node = _parse_stmt("def fetch_stats(): pass")
        assert _is_summary_function(node) is True

    def test_metrics_in_name(self):
        node = _parse_stmt("def load_metrics(): pass")
        assert _is_summary_function(node) is True

    def test_metric_in_name(self):
        node = _parse_stmt("def get_metric(): pass")
        assert _is_summary_function(node) is True

    def test_count_in_name(self):
        node = _parse_stmt("def document_count(): pass")
        assert _is_summary_function(node) is True

    def test_dashboard_in_name(self):
        node = _parse_stmt("def get_dashboard_data(): pass")
        assert _is_summary_function(node) is True

    def test_overview_in_name(self):
        node = _parse_stmt("def overview_handler(): pass")
        assert _is_summary_function(node) is True

    def test_async_function_match(self):
        node = _parse_stmt("async def get_summary(): pass")
        assert _is_summary_function(node) is True

    def test_async_function_stats(self):
        node = _parse_stmt("async def load_stats(): pass")
        assert _is_summary_function(node) is True

    def test_unrelated_name_false(self):
        node = _parse_stmt("def list_transactions(): pass")
        assert _is_summary_function(node) is False

    def test_assign_node_false(self):
        node = _parse_stmt("x = 1")
        assert _is_summary_function(node) is False

    def test_import_node_false(self):
        node = _parse_stmt("import os")
        assert _is_summary_function(node) is False

    def test_case_insensitive_upper(self):
        # Pattern uses re.I
        node = _parse_stmt("def GET_SUMMARY(): pass")
        assert _is_summary_function(node) is True

    def test_case_insensitive_mixed(self):
        node = _parse_stmt("def getDashboardStats(): pass")
        assert _is_summary_function(node) is True

    def test_name_with_no_keyword(self):
        node = _parse_stmt("def create_user(): pass")
        assert _is_summary_function(node) is False

    def test_count_as_prefix(self):
        node = _parse_stmt("def count_active_users(): pass")
        assert _is_summary_function(node) is True

    def test_metrics_as_suffix(self):
        node = _parse_stmt("def billing_metrics(): pass")
        assert _is_summary_function(node) is True


# ---------------------------------------------------------------------------
# 4. _has_data_dependency
# ---------------------------------------------------------------------------

class TestHasDataDependency:
    def test_repo_name_access(self):
        node = _parse_stmt("def fn():\n    return repo.count()")
        assert _has_data_dependency(node) is True

    def test_db_name_access(self):
        node = _parse_stmt("def fn():\n    return db.find({})")
        assert _has_data_dependency(node) is True

    def test_database_name_access(self):
        node = _parse_stmt("def fn():\n    return database.aggregate([])")
        assert _has_data_dependency(node) is True

    def test_collection_name_access(self):
        node = _parse_stmt("def fn():\n    return collection.find_one({})")
        assert _has_data_dependency(node) is True

    def test_collections_name_access(self):
        node = _parse_stmt("def fn():\n    c = collections['users']\n    return c.find({})")
        assert _has_data_dependency(node) is True

    def test_find_attribute_access(self):
        node = _parse_stmt("def fn():\n    return ctx.find({})")
        assert _has_data_dependency(node) is True

    def test_find_one_attribute_access(self):
        node = _parse_stmt("def fn():\n    return obj.find_one({'id': x})")
        assert _has_data_dependency(node) is True

    def test_aggregate_attribute_access(self):
        node = _parse_stmt("def fn():\n    return obj.aggregate(pipeline)")
        assert _has_data_dependency(node) is True

    def test_count_documents_attribute_access(self):
        node = _parse_stmt("def fn():\n    return col.count_documents({})")
        assert _has_data_dependency(node) is True

    def test_distinct_attribute_access(self):
        node = _parse_stmt("def fn():\n    return col.distinct('status')")
        assert _has_data_dependency(node) is True

    def test_estimated_document_count_attribute_access(self):
        node = _parse_stmt("def fn():\n    return col.estimated_document_count()")
        assert _has_data_dependency(node) is True

    def test_no_data_access_false(self):
        node = _parse_stmt("def fn():\n    return 42")
        assert _has_data_dependency(node) is False

    def test_arithmetic_only_false(self):
        node = _parse_stmt("def fn():\n    return a + b + c")
        assert _has_data_dependency(node) is False

    def test_string_literal_only_false(self):
        node = _parse_stmt("def fn():\n    return 'hello'")
        assert _has_data_dependency(node) is False

    def test_nested_access_found(self):
        # repo buried in nested call
        node = _parse_stmt(
            "def fn():\n    result = helper(repo.find({}))\n    return result"
        )
        assert _has_data_dependency(node) is True

    def test_attribute_db_access(self):
        # "db" is also in _DATA_ACCESS_ATTRS
        node = _parse_stmt("def fn():\n    return ctx.db.find({})")
        assert _has_data_dependency(node) is True


# ---------------------------------------------------------------------------
# 5. _literal_metric_value — ast.Constant cases
# ---------------------------------------------------------------------------

class TestLiteralMetricValueConstants:
    def test_int_is_literal(self):
        assert _literal_metric_value(_const(42)) is True

    def test_zero_is_literal(self):
        assert _literal_metric_value(_const(0)) is True

    def test_float_is_literal(self):
        assert _literal_metric_value(_const(3.14)) is True

    def test_none_is_not_literal(self):
        assert _literal_metric_value(_const(None)) is False

    def test_bool_true_is_not_literal(self):
        assert _literal_metric_value(_const(True)) is False

    def test_bool_false_is_not_literal(self):
        assert _literal_metric_value(_const(False)) is False

    def test_percent_string_is_literal(self):
        assert _literal_metric_value(_const("50%")) is True

    def test_percent_decimal_string_is_literal(self):
        assert _literal_metric_value(_const("3.5%")) is True

    def test_string_with_change_is_literal(self):
        assert _literal_metric_value(_const("no change")) is True

    def test_string_with_growth_is_literal(self):
        assert _literal_metric_value(_const("growth rate")) is True

    def test_string_with_trend_is_literal(self):
        assert _literal_metric_value(_const("upward trend")) is True

    def test_string_with_demo_is_literal(self):
        assert _literal_metric_value(_const("demo value")) is True

    def test_string_with_sample_is_literal(self):
        assert _literal_metric_value(_const("sample data")) is True

    def test_plain_string_not_literal(self):
        assert _literal_metric_value(_const("hello world")) is False

    def test_empty_string_not_literal(self):
        assert _literal_metric_value(_const("")) is False

    def test_plain_string_with_trend_context_is_literal(self):
        # When trend_context=True, any string constant returns True
        assert _literal_metric_value(_const("plain text"), trend_context=True) is True

    def test_plain_string_without_trend_context_not_literal(self):
        assert _literal_metric_value(_const("plain text"), trend_context=False) is False


# ---------------------------------------------------------------------------
# 6. _literal_metric_value — container cases
# ---------------------------------------------------------------------------

class TestLiteralMetricValueContainers:
    def test_list_with_int_is_literal(self):
        node = _parse_expr("[42, 100]")
        assert _literal_metric_value(node) is True

    def test_list_with_safe_strings_not_literal(self):
        node = _parse_expr('["active", "pending"]')
        assert _literal_metric_value(node) is False

    def test_list_with_percent_string_is_literal(self):
        node = _parse_expr('["50%"]')
        assert _literal_metric_value(node) is True

    def test_tuple_with_int_is_literal(self):
        node = _parse_expr("(1, 2, 3)")
        assert _literal_metric_value(node) is True

    def test_dict_with_trend_key_and_int_is_literal(self):
        # trend key → trend_context=True for value, int → True
        node = _parse_expr('{"trend": 5}')
        assert _literal_metric_value(node) is True

    def test_dict_with_non_trend_key_and_int_is_literal(self):
        # Non-trend key, but int is always True regardless of trend_context
        node = _parse_expr('{"count": 99}')
        assert _literal_metric_value(node) is True

    def test_dict_with_non_trend_key_and_plain_string_not_literal(self):
        # Non-trend key + plain string, trend_context stays False → False
        node = _parse_expr('{"status": "active"}')
        assert _literal_metric_value(node) is False

    def test_dict_with_trend_key_and_plain_string_is_literal(self):
        # Trend key pushes trend_context=True → plain string becomes True
        node = _parse_expr('{"change": "upward"}')
        assert _literal_metric_value(node) is True

    def test_dict_with_trend_key_and_none_not_literal(self):
        # None → False even with trend_context=True
        node = _parse_expr('{"trend": None}')
        assert _literal_metric_value(node) is False

    def test_non_ast_constant_node_not_literal(self):
        # AST Name node (variable reference) is not a literal
        node = _parse_expr("some_variable")
        assert _literal_metric_value(node) is False


# ---------------------------------------------------------------------------
# 7. _static_trend_value
# ---------------------------------------------------------------------------

class TestStaticTrendValue:
    def test_non_dict_node_false(self):
        node = _parse_expr("42")  # ast.Constant, not ast.Dict
        assert _static_trend_value(node) is False

    def test_list_not_dict_false(self):
        node = _parse_expr("[1, 2, 3]")
        assert _static_trend_value(node) is False

    def test_empty_dict_false(self):
        node = _parse_expr("{}")
        assert _static_trend_value(node) is False

    def test_dict_with_no_trend_keys_false(self):
        node = _parse_expr('{"count": 10, "status": "active"}')
        assert _static_trend_value(node) is False

    def test_dict_with_trend_key_and_none_false(self):
        # None is honest empty — not flagged
        node = _parse_expr('{"trend": None}')
        assert _static_trend_value(node) is False

    def test_dict_with_trend_key_and_int_true(self):
        node = _parse_expr('{"trend": 5}')
        assert _static_trend_value(node) is True

    def test_dict_with_change_key_and_int_true(self):
        node = _parse_expr('{"change": 3}')
        assert _static_trend_value(node) is True

    def test_dict_with_growth_key_and_float_true(self):
        node = _parse_expr('{"growth": 0.12}')
        assert _static_trend_value(node) is True

    def test_dict_with_delta_key_and_int_true(self):
        node = _parse_expr('{"delta": 7}')
        assert _static_trend_value(node) is True

    def test_dict_with_rate_key_and_int_true(self):
        node = _parse_expr('{"rate": 2}')
        assert _static_trend_value(node) is True

    def test_dict_with_trend_key_and_plain_string_true(self):
        # trend_context=True is passed, so any string is flagged as literal
        node = _parse_expr('{"trend": "upward"}')
        assert _static_trend_value(node) is True

    def test_dict_mixed_none_and_int_trend_keys_true(self):
        # One trend key → None (OK), another trend key → int (flagged)
        node = _parse_expr('{"trend": None, "change": 5}')
        assert _static_trend_value(node) is True

    def test_dict_all_trend_keys_none_false(self):
        # All trend keys have None → all safe
        node = _parse_expr('{"trend": None, "change": None}')
        assert _static_trend_value(node) is False

    def test_dict_non_trend_key_with_int_false(self):
        # Only non-trend keys — skipped entirely
        node = _parse_expr('{"total": 100}')
        # "total" doesn't match _TREND_KEY (trend|change|growth|delta|rate)
        assert _static_trend_value(node) is False

    def test_dict_case_insensitive_trend_key(self):
        # _TREND_KEY uses re.I
        node = _parse_expr('{"TREND": 10}')
        assert _static_trend_value(node) is True
