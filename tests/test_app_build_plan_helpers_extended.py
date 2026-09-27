"""
AppGenerator app_build_plan.py extended pure helper unit tests.

Covers helpers not in test_app_build_plan_helpers.py:

  _dedupe_preserving_order:
    - empty iterable → []
    - no duplicates → values preserved in order
    - None/empty items skipped
    - duplicates only first occurrence kept
    - non-string items coerced via str()

  _join_unique_text:
    - empty iterable → ""
    - single item → that item
    - duplicates deduplicated
    - custom separator applied
    - None items skipped

  _infer_module_id_from_owned_paths:
    - single module path → module_id returned
    - multiple paths same module → module_id returned
    - multiple paths different modules → None (ambiguous)
    - no paths → None
    - paths not under "modules/" → None

"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _dedupe_preserving_order,
    _infer_module_id_from_owned_paths,
    _join_unique_text,
)

# ---------------------------------------------------------------------------
# 1. _dedupe_preserving_order
# ---------------------------------------------------------------------------

class TestDedupePreservingOrder:
    def test_empty_iterable_returns_empty(self):
        assert _dedupe_preserving_order([]) == []

    def test_no_duplicates_preserves_order(self):
        result = _dedupe_preserving_order(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_none_items_skipped(self):
        result = _dedupe_preserving_order([None, "a", None])
        assert result == ["a"]

    def test_empty_string_items_skipped(self):
        result = _dedupe_preserving_order(["", "a"])
        assert result == ["a"]

    def test_whitespace_items_skipped(self):
        result = _dedupe_preserving_order(["   ", "valid"])
        assert result == ["valid"]

    def test_duplicates_first_occurrence_kept(self):
        result = _dedupe_preserving_order(["x", "y", "x", "z"])
        assert result == ["x", "y", "z"]

    def test_non_string_coerced(self):
        result = _dedupe_preserving_order([1, 2, 1])
        assert "1" in result
        assert "2" in result
        assert result.count("1") == 1


# ---------------------------------------------------------------------------
# 2. _join_unique_text
# ---------------------------------------------------------------------------

class TestJoinUniqueText:
    def test_empty_iterable_returns_empty_string(self):
        assert _join_unique_text([]) == ""

    def test_single_item_returned(self):
        assert _join_unique_text(["hello"]) == "hello"

    def test_multiple_items_joined_with_space(self):
        result = _join_unique_text(["a", "b", "c"])
        assert result == "a b c"

    def test_duplicates_removed(self):
        result = _join_unique_text(["x", "y", "x"])
        assert result == "x y"

    def test_custom_separator(self):
        result = _join_unique_text(["a", "b"], separator=", ")
        assert result == "a, b"

    def test_none_items_skipped(self):
        result = _join_unique_text([None, "a"])
        assert result == "a"


# ---------------------------------------------------------------------------
# 3. _infer_module_id_from_owned_paths
# ---------------------------------------------------------------------------

class TestInferModuleIdFromOwnedPaths:
    def test_single_module_path_returns_id(self):
        task = {"owned_paths": ["modules/tasks/backend/handler.py"]}
        assert _infer_module_id_from_owned_paths(task) == "tasks"

    def test_multiple_paths_same_module_returns_id(self):
        task = {"owned_paths": [
            "modules/tasks/backend/handler.py",
            "modules/tasks/backend/service.py",
        ]}
        assert _infer_module_id_from_owned_paths(task) == "tasks"

    def test_multiple_paths_different_modules_returns_none(self):
        task = {"owned_paths": [
            "modules/tasks/backend/handler.py",
            "modules/users/backend/handler.py",
        ]}
        assert _infer_module_id_from_owned_paths(task) is None

    def test_no_paths_returns_none(self):
        assert _infer_module_id_from_owned_paths({"owned_paths": []}) is None

    def test_non_module_paths_returns_none(self):
        task = {"owned_paths": ["services/config.py", "app.json"]}
        assert _infer_module_id_from_owned_paths(task) is None

    def test_missing_owned_paths_key_returns_none(self):
        assert _infer_module_id_from_owned_paths({}) is None


# ---------------------------------------------------------------------------
