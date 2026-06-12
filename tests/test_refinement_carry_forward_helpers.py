"""
Pure unit tests for carry-forward module ID validation helpers in
mozaiksai/control_plane/implementations/refinement_router.py.

Covers:
  RefinementTriggerRouteResolver._validate_carry_forward_module_id (static):
    - valid alphanumeric id → (True, "")
    - valid id with underscores and hyphens → (True, "")
    - empty string → (False, "empty")
    - "." → (False, reason contains "reserved")
    - ".." → (False, reason contains "reserved")
    - contains "/" → (False, reason contains "path separator")
    - contains "\\" → (False, reason contains "path separator")
    - length exactly 80 → (True, "")
    - length 81 → (False, reason contains "exceeds")
    - contains "@" → (False, reason contains "disallowed")
    - contains space → (False, reason contains "disallowed")

  RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids (static):
    - empty list → ([], [])
    - all valid → all returned, no warnings
    - one invalid → valid kept, one rejection warning
    - duplicates deduped, order preserved
    - mixed valid/invalid → valid list + warnings for invalids
    - all invalid → empty valid list, all warnings
"""
from __future__ import annotations

from mozaiksai.control_plane.implementations.refinement_router import (
    RefinementTriggerRouteResolver,
)

_validate = RefinementTriggerRouteResolver._validate_carry_forward_module_id
_sanitize = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids


# ---------------------------------------------------------------------------
# 1. _validate_carry_forward_module_id
# ---------------------------------------------------------------------------

class TestValidateCarryForwardModuleId:
    def test_valid_simple_id(self):
        is_valid, reason = _validate("billing")
        assert is_valid is True
        assert reason == ""

    def test_valid_with_underscores(self):
        is_valid, reason = _validate("my_module_id")
        assert is_valid is True
        assert reason == ""

    def test_valid_with_hyphens(self):
        is_valid, reason = _validate("my-module-id")
        assert is_valid is True
        assert reason == ""

    def test_valid_mixed_case(self):
        is_valid, reason = _validate("BillingModule")
        assert is_valid is True

    def test_valid_alphanumeric_digits(self):
        is_valid, reason = _validate("module123")
        assert is_valid is True

    def test_valid_exactly_80_chars(self):
        mid = "a" * 80
        is_valid, reason = _validate(mid)
        assert is_valid is True

    def test_empty_string_invalid(self):
        is_valid, reason = _validate("")
        assert is_valid is False
        assert "empty" in reason

    def test_single_dot_invalid(self):
        is_valid, reason = _validate(".")
        assert is_valid is False
        assert "reserved" in reason

    def test_double_dot_invalid(self):
        is_valid, reason = _validate("..")
        assert is_valid is False
        assert "reserved" in reason

    def test_contains_forward_slash_invalid(self):
        is_valid, reason = _validate("modules/billing")
        assert is_valid is False
        assert "path separator" in reason

    def test_contains_backslash_invalid(self):
        is_valid, reason = _validate("modules\\billing")
        assert is_valid is False
        assert "path separator" in reason

    def test_exceeds_80_chars_invalid(self):
        mid = "a" * 81
        is_valid, reason = _validate(mid)
        assert is_valid is False
        assert "exceeds" in reason

    def test_contains_at_sign_invalid(self):
        is_valid, reason = _validate("module@id")
        assert is_valid is False
        assert "disallowed" in reason

    def test_contains_space_invalid(self):
        is_valid, reason = _validate("module id")
        assert is_valid is False
        assert "disallowed" in reason

    def test_contains_dot_middle_invalid(self):
        # Dot in the middle is not in [a-zA-Z0-9_-]
        is_valid, reason = _validate("module.id")
        assert is_valid is False
        assert "disallowed" in reason

    def test_returns_tuple(self):
        result = _validate("valid")
        assert isinstance(result, tuple)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 2. _sanitize_carry_forward_module_ids
# ---------------------------------------------------------------------------

class TestSanitizeCarryForwardModuleIds:
    def test_empty_list_returns_empty(self):
        valid, warnings = _sanitize([])
        assert valid == []
        assert warnings == []

    def test_all_valid_returned(self):
        valid, warnings = _sanitize(["billing", "inventory"])
        assert valid == ["billing", "inventory"]
        assert warnings == []

    def test_invalid_produces_warning(self):
        valid, warnings = _sanitize(["billing", "bad/module"])
        assert valid == ["billing"]
        assert len(warnings) == 1
        assert "bad/module" in warnings[0]

    def test_duplicates_deduped(self):
        valid, warnings = _sanitize(["billing", "billing", "inventory"])
        assert valid == ["billing", "inventory"]
        assert warnings == []

    def test_order_preserved(self):
        valid, warnings = _sanitize(["inventory", "billing"])
        assert valid == ["inventory", "billing"]

    def test_all_invalid_returns_empty_valid(self):
        valid, warnings = _sanitize(["bad/one", "bad/two", ""])
        assert valid == []
        assert len(warnings) == 3

    def test_mixed_valid_invalid(self):
        valid, warnings = _sanitize(["billing", "", "inventory", "bad/path"])
        assert valid == ["billing", "inventory"]
        assert len(warnings) == 2

    def test_warning_contains_rejection_reason(self):
        _, warnings = _sanitize(["bad/path"])
        assert len(warnings) == 1
        assert "path separator" in warnings[0]

    def test_duplicate_not_counted_as_invalid(self):
        # Duplicate is silently dropped, no warning
        valid, warnings = _sanitize(["mod", "mod"])
        assert valid == ["mod"]
        assert warnings == []

    def test_returns_tuple_of_two_lists(self):
        result = _sanitize(["valid"])
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], list)
        assert isinstance(result[1], list)
