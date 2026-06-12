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

  _route_page_api_endpoint_to_facade:
    - endpoint not starting with /api/modules/ → unchanged
    - endpoint with matching rule → rewritten to facade module
    - endpoint without matching rule → unchanged
    - endpoint with trailing path suffix → suffix preserved
    - backslash path normalized
    - too few parts (no action_id) → unchanged

  _route_page_api_endpoints_to_facades:
    - dict with api_endpoint → endpoint rewritten
    - nested dict → deeply rewritten
    - list of dicts → all items processed
    - non-dict/non-list scalar → returned unchanged
    - non-api_endpoint key → recursive but unchanged if no match
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _dedupe_preserving_order,
    _infer_module_id_from_owned_paths,
    _join_unique_text,
    _route_page_api_endpoint_to_facade,
    _route_page_api_endpoints_to_facades,
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
# 4. _route_page_api_endpoint_to_facade
# ---------------------------------------------------------------------------

class TestRoutePageApiEndpointToFacade:
    def _rules(self):
        return {
            ("mozaikspay", "create_checkout"): "billing_portal",
        }

    def test_matching_rule_rewrites_endpoint(self):
        result = _route_page_api_endpoint_to_facade(
            "/api/modules/mozaikspay/create_checkout",
            self._rules(),
        )
        assert result == "/api/modules/billing_portal/create_checkout"

    def test_non_matching_rule_returns_unchanged(self):
        result = _route_page_api_endpoint_to_facade(
            "/api/modules/other_module/action",
            self._rules(),
        )
        assert result == "/api/modules/other_module/action"

    def test_endpoint_not_starting_with_api_modules_unchanged(self):
        result = _route_page_api_endpoint_to_facade(
            "/api/users/me",
            self._rules(),
        )
        assert result == "/api/users/me"

    def test_endpoint_with_trailing_suffix_preserved(self):
        rules = {("mozaikspay", "create_checkout"): "billing_portal"}
        result = _route_page_api_endpoint_to_facade(
            "/api/modules/mozaikspay/create_checkout/extra",
            rules,
        )
        assert result == "/api/modules/billing_portal/create_checkout/extra"

    def test_backslash_path_normalized(self):
        rules = {("mozaikspay", "create_checkout"): "billing_portal"}
        result = _route_page_api_endpoint_to_facade(
            "\\api\\modules\\mozaikspay\\create_checkout",
            rules,
        )
        assert "billing_portal" in result

    def test_too_few_parts_returns_unchanged(self):
        result = _route_page_api_endpoint_to_facade(
            "/api/modules/only_module",
            self._rules(),
        )
        assert result == "/api/modules/only_module"

    def test_empty_rules_returns_unchanged(self):
        result = _route_page_api_endpoint_to_facade(
            "/api/modules/mozaikspay/create_checkout",
            {},
        )
        assert result == "/api/modules/mozaikspay/create_checkout"


# ---------------------------------------------------------------------------
# 5. _route_page_api_endpoints_to_facades
# ---------------------------------------------------------------------------

class TestRoutePageApiEndpointsToFacades:
    def _rules(self):
        return {("mozaikspay", "create_checkout"): "billing_portal"}

    def test_dict_with_api_endpoint_rewritten(self):
        result = _route_page_api_endpoints_to_facades(
            {"api_endpoint": "/api/modules/mozaikspay/create_checkout"},
            self._rules(),
        )
        assert result["api_endpoint"] == "/api/modules/billing_portal/create_checkout"

    def test_nested_dict_deeply_rewritten(self):
        data = {"section": {"api_endpoint": "/api/modules/mozaikspay/create_checkout"}}
        result = _route_page_api_endpoints_to_facades(data, self._rules())
        assert result["section"]["api_endpoint"] == "/api/modules/billing_portal/create_checkout"

    def test_list_of_dicts_all_processed(self):
        data = [
            {"api_endpoint": "/api/modules/mozaikspay/create_checkout"},
            {"api_endpoint": "/api/modules/other/action"},
        ]
        result = _route_page_api_endpoints_to_facades(data, self._rules())
        assert result[0]["api_endpoint"] == "/api/modules/billing_portal/create_checkout"
        assert result[1]["api_endpoint"] == "/api/modules/other/action"

    def test_non_dict_non_list_scalar_unchanged(self):
        assert _route_page_api_endpoints_to_facades("scalar", self._rules()) == "scalar"
        assert _route_page_api_endpoints_to_facades(42, self._rules()) == 42

    def test_non_api_endpoint_key_passed_through(self):
        data = {"name": "Checkout", "other_key": "val"}
        result = _route_page_api_endpoints_to_facades(data, self._rules())
        assert result["name"] == "Checkout"
        assert result["other_key"] == "val"
