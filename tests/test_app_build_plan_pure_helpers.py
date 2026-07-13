"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/app_build_plan.py

Covers helpers NOT already tested in test_appgenerator_managed_capability_smoke.py:

  _normalize_string_list:
    - non-list → []
    - list items stringified and stripped
    - empty/whitespace items excluded
    - valid items included

  _normalize_object_list:
    - non-list → []
    - non-dict items skipped
    - dict items included as copies

  _normalize_context_variables:
    - None → None
    - dict → copy returned
    - non-list, non-dict → None
    - list of key/value dicts → flattened dict
    - value_type "boolean": "true"/"yes"/"1" → True; "false" → False
    - value_type "integer": valid int → int; invalid → 0
    - value_type "number": valid float → float; invalid → 0.0
    - value_type "null" → None
    - value_type "json": valid JSON → parsed; invalid → raw_value
    - default value_type "string": None raw_value → ""

  _task_sort_key:
    - task with task_id → (0, task_id)
    - task without task_id → (1, "")

  _infer_pack_id_from_integration_path:
    - services/integrations/{id}_client.py → pack_id returned
    - wrong prefix → None
    - no _client.py suffix → None
    - backslash path normalized

  _dedupe_preserving_order:
    - empty iterable → []
    - duplicates removed
    - order preserved
    - falsy values (None, "") → excluded
    - whitespace-only → excluded

  _join_unique_text:
    - empty → ""
    - single item → that item
    - duplicates merged into one word
    - custom separator used

  _pack_id_from_descriptor:
    - "capability_pack_id" key → returned
    - falls back to "id"
    - falls back to "pack_id"
    - empty dict → ""

  _context_get:
    - None context → default returned
    - dict context → value returned
    - missing key → default returned
    - non-dict/non-None without .get → default returned

  _route_page_api_endpoint_to_facade:
    - endpoint not starting with /api/modules/ → unchanged
    - matching rule → module replaced
    - no matching rule → unchanged
    - less than 2 parts after prefix → unchanged

  _route_page_api_endpoints_to_facades:
    - dict with api_endpoint key → endpoint routed
    - nested dict → recursed
    - list → all items processed
    - non-endpoint string key → unchanged
    - non-string api_endpoint → unchanged

  _iter_page_api_endpoints:
    - dict with api_endpoint → yielded
    - nested dict → recursed
    - list → recursed
    - non-dict non-list → nothing yielded
    - whitespace-only endpoint → not yielded
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _context_get,
    _dedupe_preserving_order,
    _infer_pack_id_from_integration_path,
    _iter_page_api_endpoints,
    _join_unique_text,
    _normalize_context_variables,
    _normalize_object_list,
    _normalize_string_list,
    _pack_id_from_descriptor,
    _route_page_api_endpoint_to_facade,
    _route_page_api_endpoints_to_facades,
    _task_sort_key,
)

# ---------------------------------------------------------------------------
# 1. _normalize_string_list
# ---------------------------------------------------------------------------

class TestNormalizeStringList:
    def test_non_list_returns_empty(self):
        assert _normalize_string_list("not-a-list") == []

    def test_none_returns_empty(self):
        assert _normalize_string_list(None) == []

    def test_dict_returns_empty(self):
        assert _normalize_string_list({}) == []

    def test_items_stringified_and_stripped(self):
        result = _normalize_string_list(["  hello  ", "  world  "])
        assert result == ["hello", "world"]

    def test_whitespace_only_excluded(self):
        result = _normalize_string_list(["  ", ""])
        assert result == []

    def test_integer_items_stringified(self):
        result = _normalize_string_list([42, 7])
        assert "42" in result
        assert "7" in result

    def test_valid_strings_included(self):
        result = _normalize_string_list(["a", "b", "c"])
        assert result == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# 2. _normalize_object_list
# ---------------------------------------------------------------------------

class TestNormalizeObjectList:
    def test_non_list_returns_empty(self):
        assert _normalize_object_list("not-a-list") == []

    def test_none_returns_empty(self):
        assert _normalize_object_list(None) == []

    def test_non_dict_items_skipped(self):
        result = _normalize_object_list(["string", 42, None])
        assert result == []

    def test_dict_items_included(self):
        result = _normalize_object_list([{"id": "task1"}, {"id": "task2"}])
        assert len(result) == 2

    def test_dict_items_are_copies(self):
        original = {"id": "task1"}
        result = _normalize_object_list([original])
        assert result[0] is not original
        assert result[0] == original

    def test_mixed_list(self):
        result = _normalize_object_list([{"id": "t1"}, "skip", {"id": "t2"}])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 3. _normalize_context_variables
# ---------------------------------------------------------------------------

class TestNormalizeContextVariables:
    def test_none_returns_none(self):
        assert _normalize_context_variables(None) is None

    def test_dict_returns_copy(self):
        d = {"key": "val"}
        result = _normalize_context_variables(d)
        assert result == d
        assert result is not d

    def test_non_list_non_dict_returns_none(self):
        assert _normalize_context_variables("string") is None
        assert _normalize_context_variables(42) is None

    def test_list_flattened_to_dict(self):
        items = [{"key": "feature", "value": "payments"}]
        result = _normalize_context_variables(items)
        assert result["feature"] == "payments"

    def test_boolean_true_values(self):
        for val in ("true", "yes", "1", "on"):
            items = [{"key": "flag", "value": val, "value_type": "boolean"}]
            result = _normalize_context_variables(items)
            assert result["flag"] is True, f"Expected True for value={val!r}"

    def test_boolean_false_value(self):
        items = [{"key": "flag", "value": "false", "value_type": "boolean"}]
        result = _normalize_context_variables(items)
        assert result["flag"] is False

    def test_integer_valid(self):
        items = [{"key": "count", "value": "42", "value_type": "integer"}]
        result = _normalize_context_variables(items)
        assert result["count"] == 42
        assert isinstance(result["count"], int)

    def test_integer_invalid_returns_zero(self):
        items = [{"key": "count", "value": "abc", "value_type": "integer"}]
        result = _normalize_context_variables(items)
        assert result["count"] == 0

    def test_number_valid(self):
        items = [{"key": "rate", "value": "3.14", "value_type": "number"}]
        result = _normalize_context_variables(items)
        assert abs(result["rate"] - 3.14) < 0.001

    def test_number_invalid_returns_zero_float(self):
        items = [{"key": "rate", "value": "bad", "value_type": "number"}]
        result = _normalize_context_variables(items)
        assert result["rate"] == 0.0

    def test_null_type_returns_none(self):
        items = [{"key": "nothing", "value": "something", "value_type": "null"}]
        result = _normalize_context_variables(items)
        assert result["nothing"] is None

    def test_json_type_valid(self):
        items = [{"key": "data", "value": '{"x": 1}', "value_type": "json"}]
        result = _normalize_context_variables(items)
        assert result["data"] == {"x": 1}

    def test_json_type_invalid_returns_raw(self):
        items = [{"key": "data", "value": "not-json", "value_type": "json"}]
        result = _normalize_context_variables(items)
        assert result["data"] == "not-json"

    def test_default_string_type_none_value_returns_empty(self):
        items = [{"key": "text", "value": None}]
        result = _normalize_context_variables(items)
        assert result["text"] == ""

    def test_item_without_key_skipped(self):
        items = [{"value": "orphan"}]
        result = _normalize_context_variables(items)
        assert result == {}

    def test_non_dict_item_in_list_skipped(self):
        items = ["not-a-dict", {"key": "k", "value": "v"}]
        result = _normalize_context_variables(items)
        assert "k" in result


# ---------------------------------------------------------------------------
# 4. _task_sort_key
# ---------------------------------------------------------------------------

class TestTaskSortKey:
    def test_task_with_id_returns_zero_prefix(self):
        key = _task_sort_key({"task_id": "persistence_contract"})
        assert key[0] == 0
        assert key[1] == "persistence_contract"

    def test_task_without_id_returns_one_prefix(self):
        key = _task_sort_key({})
        assert key[0] == 1
        assert key[1] == ""

    def test_task_with_none_id_returns_one_prefix(self):
        key = _task_sort_key({"task_id": None})
        assert key[0] == 1

    def test_sort_key_orders_tasks_with_id_before_without(self):
        tasks = [{}, {"task_id": "abc"}]
        sorted_tasks = sorted(tasks, key=_task_sort_key)
        assert sorted_tasks[0]["task_id"] == "abc"


# ---------------------------------------------------------------------------
# 5. _infer_pack_id_from_integration_path
# ---------------------------------------------------------------------------

class TestInferPackIdFromIntegrationPath:
    def test_valid_integration_path(self):
        result = _infer_pack_id_from_integration_path("services/integrations/payment_provider_pay_client.py")
        assert result == "payment_provider_pay"

    def test_wrong_prefix_returns_none(self):
        assert _infer_pack_id_from_integration_path("services/adapters/payment_provider_client.py") is None

    def test_no_client_suffix_returns_none(self):
        assert _infer_pack_id_from_integration_path("services/integrations/payment_provider.py") is None

    def test_backslash_normalized(self):
        result = _infer_pack_id_from_integration_path("services\\integrations\\mailchimp_client.py")
        assert result == "mailchimp"

    def test_empty_string_returns_none(self):
        assert _infer_pack_id_from_integration_path("") is None

    def test_unrelated_path_returns_none(self):
        assert _infer_pack_id_from_integration_path("modules/orders/backend/handler.py") is None


# ---------------------------------------------------------------------------
# 6. _dedupe_preserving_order
# ---------------------------------------------------------------------------

class TestDedupePreservingOrder:
    def test_empty_returns_empty(self):
        assert _dedupe_preserving_order([]) == []

    def test_duplicates_removed(self):
        result = _dedupe_preserving_order(["a", "b", "a"])
        assert result == ["a", "b"]

    def test_order_preserved(self):
        result = _dedupe_preserving_order(["c", "a", "b"])
        assert result == ["c", "a", "b"]

    def test_none_values_excluded(self):
        result = _dedupe_preserving_order([None, "a", None])
        assert result == ["a"]

    def test_empty_string_excluded(self):
        result = _dedupe_preserving_order(["", "a"])
        assert result == ["a"]

    def test_whitespace_only_excluded(self):
        result = _dedupe_preserving_order(["  ", "a"])
        assert result == ["a"]

    def test_integers_stringified(self):
        result = _dedupe_preserving_order([1, 2, 1])
        assert result == ["1", "2"]


# ---------------------------------------------------------------------------
# 7. _join_unique_text
# ---------------------------------------------------------------------------

class TestJoinUniqueText:
    def test_empty_returns_empty_string(self):
        assert _join_unique_text([]) == ""

    def test_single_item_returned(self):
        assert _join_unique_text(["hello"]) == "hello"

    def test_duplicates_merged(self):
        result = _join_unique_text(["hello", "hello"])
        assert result == "hello"

    def test_custom_separator(self):
        result = _join_unique_text(["a", "b"], separator=", ")
        assert result == "a, b"

    def test_default_space_separator(self):
        result = _join_unique_text(["hello", "world"])
        assert result == "hello world"

    def test_none_values_excluded(self):
        result = _join_unique_text([None, "hello"])
        assert result == "hello"


# ---------------------------------------------------------------------------
# 8. _pack_id_from_descriptor
# ---------------------------------------------------------------------------

class TestPackIdFromDescriptor:
    def test_capability_pack_id_returned(self):
        assert _pack_id_from_descriptor({"capability_pack_id": "payment_provider"}) == "payment_provider"

    def test_falls_back_to_id(self):
        assert _pack_id_from_descriptor({"id": "paypal"}) == "paypal"

    def test_falls_back_to_pack_id(self):
        assert _pack_id_from_descriptor({"pack_id": "sendgrid"}) == "sendgrid"

    def test_empty_dict_returns_empty_string(self):
        assert _pack_id_from_descriptor({}) == ""

    def test_capability_pack_id_takes_priority(self):
        d = {"capability_pack_id": "payment_provider", "id": "other", "pack_id": "third"}
        assert _pack_id_from_descriptor(d) == "payment_provider"

    def test_whitespace_stripped(self):
        assert _pack_id_from_descriptor({"capability_pack_id": "  payment_provider  "}) == "payment_provider"


# ---------------------------------------------------------------------------
# 9. _context_get
# ---------------------------------------------------------------------------

class TestContextGet:
    def test_none_context_returns_default(self):
        assert _context_get(None, "key") is None
        assert _context_get(None, "key", "fallback") == "fallback"

    def test_dict_context_key_found(self):
        ctx = {"my_key": "my_value"}
        assert _context_get(ctx, "my_key") == "my_value"

    def test_dict_context_missing_key_returns_default(self):
        assert _context_get({}, "missing", "default") == "default"

    def test_non_dict_without_get_returns_default(self):
        assert _context_get("not-a-dict", "key", "fallback") == "fallback"

    def test_integer_context_returns_default(self):
        assert _context_get(42, "key", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# 10. _route_page_api_endpoint_to_facade
# ---------------------------------------------------------------------------

class TestRoutePageApiEndpointToFacade:
    def test_non_api_modules_endpoint_unchanged(self):
        rules = {}
        endpoint = "/api/auth/login"
        assert _route_page_api_endpoint_to_facade(endpoint, rules) == endpoint

    def test_matching_rule_replaces_module(self):
        rules = {("payment_provider_module", "charge"): "payment_facade"}
        result = _route_page_api_endpoint_to_facade("/api/modules/payment_provider_module/charge", rules)
        assert result == "/api/modules/payment_facade/charge"

    def test_no_matching_rule_unchanged(self):
        rules = {("other_module", "action"): "facade"}
        result = _route_page_api_endpoint_to_facade("/api/modules/payment_provider_module/charge", rules)
        assert result == "/api/modules/payment_provider_module/charge"

    def test_less_than_two_parts_unchanged(self):
        # /api/modules/only_one_segment
        result = _route_page_api_endpoint_to_facade("/api/modules/single", {})
        assert result == "/api/modules/single"

    def test_extra_path_segments_preserved(self):
        rules = {("payment_provider", "charge"): "pay_facade"}
        result = _route_page_api_endpoint_to_facade("/api/modules/payment_provider/charge/extra", rules)
        assert result == "/api/modules/pay_facade/charge/extra"

    def test_backslash_normalized(self):
        rules = {("mod", "act"): "facade"}
        result = _route_page_api_endpoint_to_facade("\\api\\modules\\mod\\act", rules)
        assert "facade" in result


# ---------------------------------------------------------------------------
# 11. _route_page_api_endpoints_to_facades
# ---------------------------------------------------------------------------

class TestRoutePageApiEndpointsToFacades:
    def test_dict_api_endpoint_rewritten(self):
        rules = {("mod", "action"): "facade"}
        data = {"api_endpoint": "/api/modules/mod/action"}
        result = _route_page_api_endpoints_to_facades(data, rules)
        assert result["api_endpoint"] == "/api/modules/facade/action"

    def test_nested_dict_recursed(self):
        rules = {("mod", "act"): "facade"}
        data = {"section": {"api_endpoint": "/api/modules/mod/act"}}
        result = _route_page_api_endpoints_to_facades(data, rules)
        assert result["section"]["api_endpoint"] == "/api/modules/facade/act"

    def test_list_items_processed(self):
        rules = {("mod", "act"): "facade"}
        data = [{"api_endpoint": "/api/modules/mod/act"}]
        result = _route_page_api_endpoints_to_facades(data, rules)
        assert result[0]["api_endpoint"] == "/api/modules/facade/act"

    def test_non_endpoint_key_unchanged(self):
        rules = {("mod", "act"): "facade"}
        data = {"name": "unchanged"}
        result = _route_page_api_endpoints_to_facades(data, rules)
        assert result["name"] == "unchanged"

    def test_non_string_api_endpoint_unchanged(self):
        rules = {("mod", "act"): "facade"}
        data = {"api_endpoint": 42}
        result = _route_page_api_endpoints_to_facades(data, rules)
        assert result["api_endpoint"] == 42

    def test_non_dict_non_list_returned_as_is(self):
        result = _route_page_api_endpoints_to_facades("string", {})
        assert result == "string"

    def test_empty_rules_all_unchanged(self):
        data = {"api_endpoint": "/api/modules/mod/act"}
        result = _route_page_api_endpoints_to_facades(data, {})
        assert result["api_endpoint"] == "/api/modules/mod/act"


# ---------------------------------------------------------------------------
# 12. _iter_page_api_endpoints
# ---------------------------------------------------------------------------

class TestIterPageApiEndpoints:
    def test_dict_with_api_endpoint_yielded(self):
        result = list(_iter_page_api_endpoints({"api_endpoint": "/api/modules/orders/list"}))
        assert "/api/modules/orders/list" in result

    def test_nested_dict_recursed(self):
        data = {"section": {"api_endpoint": "/api/modules/orders/get"}}
        result = list(_iter_page_api_endpoints(data))
        assert "/api/modules/orders/get" in result

    def test_list_recursed(self):
        data = [{"api_endpoint": "/api/modules/a/b"}, {"api_endpoint": "/api/modules/c/d"}]
        result = list(_iter_page_api_endpoints(data))
        assert "/api/modules/a/b" in result
        assert "/api/modules/c/d" in result

    def test_non_dict_non_list_yields_nothing(self):
        assert list(_iter_page_api_endpoints("string")) == []
        assert list(_iter_page_api_endpoints(42)) == []

    def test_whitespace_only_endpoint_not_yielded(self):
        result = list(_iter_page_api_endpoints({"api_endpoint": "   "}))
        assert result == []

    def test_non_string_api_endpoint_not_yielded(self):
        result = list(_iter_page_api_endpoints({"api_endpoint": None}))
        assert result == []

    def test_non_endpoint_key_not_yielded(self):
        result = list(_iter_page_api_endpoints({"name": "/api/modules/mod/act"}))
        assert result == []

    def test_empty_dict_yields_nothing(self):
        assert list(_iter_page_api_endpoints({})) == []
