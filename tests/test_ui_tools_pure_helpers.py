"""
Pure helper unit tests for:
  mozaiksai/core/workflow/ui_tools.py

Covers sync pure helpers (no IO/async):

  _format_action_label:
    - empty string → "Action"
    - None → "Action"
    - underscore-separated id → capitalized words joined with space
    - hyphen-separated id → capitalized words joined with space
    - mixed underscore+hyphen → capitalized words joined with space
    - single word → capitalized
    - all underscores → "Action"
    - leading/trailing underscores stripped
    - already capitalized → correctly formatted

  _normalize_manifest_action:
    - non-dict → None
    - dict without "id" → None
    - dict with empty id → None
    - dict with whitespace-only id → None
    - minimal valid dict → normalized with id and auto-label
    - explicit label used when provided
    - label falls back to _format_action_label when absent
    - description included only when non-empty
    - variant included only when non-empty
    - approved (bool True) included
    - approved (bool False) included
    - approved missing → not in result
    - approved non-bool → not included
    - payload_schema (non-empty dict) included as deepcopy
    - payload_schema empty dict → not included
    - payload_schema non-dict → not included
    - payload (non-empty dict) included as deepcopy
    - payload empty dict → not included
    - id stripped of whitespace
"""
from __future__ import annotations

from mozaiksai.core.workflow.ui_tools import (
    _format_action_label,
    _normalize_manifest_action,
)

# ---------------------------------------------------------------------------
# 1. _format_action_label
# ---------------------------------------------------------------------------

class TestFormatActionLabel:
    def test_empty_string_returns_action(self):
        assert _format_action_label("") == "Action"

    def test_none_returns_action(self):
        assert _format_action_label(None) == "Action"

    def test_underscore_separated(self):
        assert _format_action_label("create_billing") == "Create Billing"

    def test_hyphen_separated(self):
        assert _format_action_label("create-billing") == "Create Billing"

    def test_mixed_underscore_hyphen(self):
        assert _format_action_label("create-billing_module") == "Create Billing Module"

    def test_single_word_capitalized(self):
        assert _format_action_label("submit") == "Submit"

    def test_all_underscores_returns_action(self):
        assert _format_action_label("___") == "Action"

    def test_leading_trailing_underscores_stripped(self):
        assert _format_action_label("_create_billing_") == "Create Billing"

    def test_multiple_consecutive_underscores_collapsed(self):
        assert _format_action_label("create__billing") == "Create Billing"

    def test_already_uppercase_word(self):
        assert _format_action_label("SUBMIT") == "Submit"

    def test_three_words(self):
        assert _format_action_label("get_user_profile") == "Get User Profile"


# ---------------------------------------------------------------------------
# 2. _normalize_manifest_action
# ---------------------------------------------------------------------------

class TestNormalizeManifestAction:
    def test_non_dict_returns_none(self):
        assert _normalize_manifest_action("not-a-dict") is None
        assert _normalize_manifest_action(42) is None
        assert _normalize_manifest_action(None) is None

    def test_dict_without_id_returns_none(self):
        assert _normalize_manifest_action({"label": "Submit"}) is None

    def test_dict_with_empty_id_returns_none(self):
        assert _normalize_manifest_action({"id": ""}) is None

    def test_dict_with_whitespace_only_id_returns_none(self):
        assert _normalize_manifest_action({"id": "   "}) is None

    def test_minimal_valid_returns_normalized(self):
        result = _normalize_manifest_action({"id": "submit_order"})
        assert result is not None
        assert result["id"] == "submit_order"
        assert result["label"] == "Submit Order"

    def test_explicit_label_used(self):
        result = _normalize_manifest_action({"id": "submit", "label": "Place Order"})
        assert result["label"] == "Place Order"

    def test_label_stripped(self):
        result = _normalize_manifest_action({"id": "submit", "label": "  Place Order  "})
        assert result["label"] == "Place Order"

    def test_empty_label_falls_back_to_format(self):
        result = _normalize_manifest_action({"id": "submit_order", "label": ""})
        assert result["label"] == "Submit Order"

    def test_description_included_when_non_empty(self):
        result = _normalize_manifest_action({"id": "submit", "description": "Submit the order"})
        assert result["description"] == "Submit the order"

    def test_description_not_included_when_empty(self):
        result = _normalize_manifest_action({"id": "submit", "description": ""})
        assert "description" not in result

    def test_description_not_included_when_whitespace_only(self):
        result = _normalize_manifest_action({"id": "submit", "description": "   "})
        assert "description" not in result

    def test_variant_included_when_non_empty(self):
        result = _normalize_manifest_action({"id": "submit", "variant": "danger"})
        assert result["variant"] == "danger"

    def test_variant_not_included_when_empty(self):
        result = _normalize_manifest_action({"id": "submit", "variant": ""})
        assert "variant" not in result

    def test_approved_true_included(self):
        result = _normalize_manifest_action({"id": "submit", "approved": True})
        assert result["approved"] is True

    def test_approved_false_included(self):
        result = _normalize_manifest_action({"id": "submit", "approved": False})
        assert result["approved"] is False

    def test_approved_missing_not_in_result(self):
        result = _normalize_manifest_action({"id": "submit"})
        assert "approved" not in result

    def test_approved_non_bool_not_included(self):
        result = _normalize_manifest_action({"id": "submit", "approved": "yes"})
        assert "approved" not in result

    def test_payload_schema_included_when_non_empty_dict(self):
        schema = {"type": "object", "properties": {"amount": {"type": "number"}}}
        result = _normalize_manifest_action({"id": "submit", "payload_schema": schema})
        assert result["payload_schema"] == schema

    def test_payload_schema_is_deepcopy(self):
        schema = {"type": "object"}
        result = _normalize_manifest_action({"id": "submit", "payload_schema": schema})
        assert result["payload_schema"] is not schema

    def test_payload_schema_empty_dict_not_included(self):
        result = _normalize_manifest_action({"id": "submit", "payload_schema": {}})
        assert "payload_schema" not in result

    def test_payload_schema_non_dict_not_included(self):
        result = _normalize_manifest_action({"id": "submit", "payload_schema": "schema"})
        assert "payload_schema" not in result

    def test_payload_included_when_non_empty_dict(self):
        payload = {"item_id": "abc", "qty": 1}
        result = _normalize_manifest_action({"id": "submit", "payload": payload})
        assert result["payload"] == payload

    def test_payload_empty_dict_not_included(self):
        result = _normalize_manifest_action({"id": "submit", "payload": {}})
        assert "payload" not in result

    def test_id_stripped_of_whitespace(self):
        result = _normalize_manifest_action({"id": "  submit  "})
        assert result["id"] == "submit"
