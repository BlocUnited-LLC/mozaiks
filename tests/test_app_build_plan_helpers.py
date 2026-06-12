"""
AppGenerator app_build_plan.py pure helper unit tests.

Covers:
  _normalize_string_list:
    - non-list → []
    - list of strings → stripped
    - whitespace-only items excluded
    - non-string items coerced via str()

  _normalize_object_list:
    - non-list → []
    - list of dicts → shallow copies returned
    - non-dict items skipped
    - empty list → []

  _normalize_context_variables:
    - None → None
    - dict → shallow copy returned
    - non-dict/non-list → None
    - list of key/value pairs → dict built
    - boolean value_type: truthy strings → True
    - boolean value_type: falsy strings → False
    - integer value_type: valid int string → int
    - integer value_type: invalid → 0
    - number value_type: valid float → float
    - null value_type → None
    - json value_type: valid JSON → parsed
    - json value_type: invalid JSON → raw value
    - default (string) value_type → str
    - missing key skipped

  _unwrap_app_build_plan_payload:
    - payload with "app_kind" key → returned as-is
    - nested under "AppBuildPlan" key → unwrapped
    - nested under "app_build_plan" key → unwrapped
    - single-key dict with nested value containing app_kind → unwrapped
    - payload without app_kind → returned unchanged

  _task_sort_key:
    - task with task_id → (0, task_id)
    - task without task_id → (1, "")

  _infer_pack_id_from_integration_path:
    - services/integrations/{id}_client.py → id returned
    - non-integration path → None
    - path without _client.py suffix → None
    - backslash path normalized
    - deeply nested (not matching prefix) → None
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _infer_pack_id_from_integration_path,
    _normalize_context_variables,
    _normalize_object_list,
    _normalize_string_list,
    _task_sort_key,
    _unwrap_app_build_plan_payload,
)

# ---------------------------------------------------------------------------
# 1. _normalize_string_list
# ---------------------------------------------------------------------------

class TestNormalizeStringList:
    def test_non_list_returns_empty(self):
        assert _normalize_string_list(None) == []
        assert _normalize_string_list("string") == []
        assert _normalize_string_list(42) == []

    def test_list_of_strings_stripped(self):
        assert _normalize_string_list(["  a  ", "  b  "]) == ["a", "b"]

    def test_whitespace_only_excluded(self):
        assert _normalize_string_list(["   ", "valid"]) == ["valid"]

    def test_non_string_items_coerced(self):
        result = _normalize_string_list([123, True, "str"])
        assert "123" in result
        assert "True" in result
        assert "str" in result

    def test_empty_list_returns_empty(self):
        assert _normalize_string_list([]) == []


# ---------------------------------------------------------------------------
# 2. _normalize_object_list
# ---------------------------------------------------------------------------

class TestNormalizeObjectList:
    def test_non_list_returns_empty(self):
        assert _normalize_object_list(None) == []
        assert _normalize_object_list("string") == []

    def test_list_of_dicts_returned(self):
        data = [{"a": 1}, {"b": 2}]
        result = _normalize_object_list(data)
        assert len(result) == 2
        assert result[0] == {"a": 1}
        assert result[1] == {"b": 2}

    def test_non_dict_items_skipped(self):
        result = _normalize_object_list(["string", {"key": "val"}])
        assert len(result) == 1
        assert result[0] == {"key": "val"}

    def test_returns_shallow_copies(self):
        original = {"key": "val"}
        result = _normalize_object_list([original])
        result[0]["key"] = "modified"
        assert original["key"] == "val"  # original unchanged

    def test_empty_list_returns_empty(self):
        assert _normalize_object_list([]) == []


# ---------------------------------------------------------------------------
# 3. _normalize_context_variables
# ---------------------------------------------------------------------------

class TestNormalizeContextVariables:
    def test_none_returns_none(self):
        assert _normalize_context_variables(None) is None

    def test_dict_returns_shallow_copy(self):
        data = {"key": "val"}
        result = _normalize_context_variables(data)
        assert result == {"key": "val"}

    def test_non_dict_non_list_returns_none(self):
        assert _normalize_context_variables("string") is None
        assert _normalize_context_variables(42) is None

    def test_list_of_key_value_pairs_built(self):
        data = [{"key": "greeting", "value": "hello"}]
        result = _normalize_context_variables(data)
        assert result == {"greeting": "hello"}

    def test_boolean_truthy_string(self):
        data = [{"key": "flag", "value": "true", "value_type": "boolean"}]
        result = _normalize_context_variables(data)
        assert result["flag"] is True

    def test_boolean_falsy_string(self):
        data = [{"key": "flag", "value": "false", "value_type": "boolean"}]
        result = _normalize_context_variables(data)
        assert result["flag"] is False

    def test_integer_valid(self):
        data = [{"key": "count", "value": "42", "value_type": "integer"}]
        result = _normalize_context_variables(data)
        assert result["count"] == 42

    def test_integer_invalid_returns_zero(self):
        data = [{"key": "count", "value": "not_int", "value_type": "integer"}]
        result = _normalize_context_variables(data)
        assert result["count"] == 0

    def test_number_valid_float(self):
        data = [{"key": "price", "value": "3.14", "value_type": "number"}]
        result = _normalize_context_variables(data)
        assert result["price"] == 3.14

    def test_null_value_type(self):
        data = [{"key": "empty", "value": "anything", "value_type": "null"}]
        result = _normalize_context_variables(data)
        assert result["empty"] is None

    def test_json_value_type_valid(self):
        data = [{"key": "config", "value": '{"debug": true}', "value_type": "json"}]
        result = _normalize_context_variables(data)
        assert result["config"] == {"debug": True}

    def test_json_value_type_invalid_returns_raw(self):
        data = [{"key": "config", "value": "{broken", "value_type": "json"}]
        result = _normalize_context_variables(data)
        assert result["config"] == "{broken"

    def test_default_string_value_type(self):
        data = [{"key": "name", "value": "Alice", "value_type": "string"}]
        result = _normalize_context_variables(data)
        assert result["name"] == "Alice"

    def test_missing_key_skipped(self):
        data = [{"value": "orphan"}]
        result = _normalize_context_variables(data)
        assert result == {}

    def test_non_dict_list_items_skipped(self):
        data = ["not_a_dict", {"key": "valid", "value": "x"}]
        result = _normalize_context_variables(data)
        assert result == {"valid": "x"}


# ---------------------------------------------------------------------------
# 4. _unwrap_app_build_plan_payload
# ---------------------------------------------------------------------------

class TestUnwrapAppBuildPlanPayload:
    def test_payload_with_app_kind_returned_as_is(self):
        payload = {"app_kind": "saas", "features": []}
        assert _unwrap_app_build_plan_payload(payload) is payload

    def test_nested_under_app_build_plan_key(self):
        inner = {"app_kind": "marketplace", "features": []}
        payload = {"AppBuildPlan": inner}
        assert _unwrap_app_build_plan_payload(payload) == inner

    def test_nested_under_lowercase_app_build_plan_key(self):
        inner = {"app_kind": "saas", "features": []}
        payload = {"app_build_plan": inner}
        assert _unwrap_app_build_plan_payload(payload) == inner

    def test_single_key_dict_with_app_kind_nested(self):
        inner = {"app_kind": "internal", "features": []}
        payload = {"result": inner}
        assert _unwrap_app_build_plan_payload(payload) == inner

    def test_payload_without_app_kind_returned_unchanged(self):
        payload = {"no_app_kind_here": "value"}
        assert _unwrap_app_build_plan_payload(payload) is payload


# ---------------------------------------------------------------------------
# 5. _task_sort_key
# ---------------------------------------------------------------------------

class TestTaskSortKey:
    def test_task_with_task_id_returns_zero_prefix(self):
        task = {"task_id": "task-001", "type": "module_contract"}
        key = _task_sort_key(task)
        assert key == (0, "task-001")

    def test_task_without_task_id_returns_one_prefix(self):
        task = {"type": "module_contract"}
        key = _task_sort_key(task)
        assert key == (1, "")

    def test_task_id_none_returns_one_prefix(self):
        task = {"task_id": None}
        key = _task_sort_key(task)
        assert key == (1, "")


# ---------------------------------------------------------------------------
# 6. _infer_pack_id_from_integration_path
# ---------------------------------------------------------------------------

class TestInferPackIdFromIntegrationPath:
    def test_canonical_integration_path_returns_id(self):
        result = _infer_pack_id_from_integration_path(
            "services/integrations/mozaikspay_client.py"
        )
        assert result == "mozaikspay"

    def test_non_integration_path_returns_none(self):
        assert _infer_pack_id_from_integration_path("services/other/client.py") is None

    def test_path_without_client_suffix_returns_none(self):
        assert _infer_pack_id_from_integration_path("services/integrations/mozaikspay.py") is None

    def test_backslash_path_normalized(self):
        result = _infer_pack_id_from_integration_path(
            "services\\integrations\\mozaikspay_client.py"
        )
        assert result == "mozaikspay"

    def test_deeply_nested_non_matching_path_returns_none(self):
        assert _infer_pack_id_from_integration_path(
            "modules/payments/services/integrations/stripe_client.py"
        ) is None

    def test_whitespace_stripped_before_check(self):
        result = _infer_pack_id_from_integration_path(
            "  services/integrations/wallet_client.py  "
        )
        assert result == "wallet"
