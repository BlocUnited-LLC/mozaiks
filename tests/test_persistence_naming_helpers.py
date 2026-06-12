"""
Persistence naming and startup policy pure helper unit tests.

Covers:
  naming.py:
    safe_identifier:
      - plain lowercase alphanumeric → unchanged
      - uppercase → lowercased
      - unicode normalized to ASCII
      - special chars replaced with underscores
      - consecutive underscores collapsed
      - leading/trailing underscores stripped
      - empty input → default
      - None input → default
      - result truncated to max_length

    short_stable_hash:
      - returns string of specified length
      - same input → same hash (deterministic)
      - different inputs → different hashes
      - empty input → raises ValueError

    collection_name_for:
      - builds deterministic name
      - uses app_hash in the name
      - special chars in app_slug, module_id, entity_name → sanitized
      - long names truncated to 120 chars
      - missing app_id → ValueError
      - missing module_id → ValueError
      - missing entity_name → ValueError

    scope_filter_for:
      - returns {"app_id": ...} with normalized app_id
      - extra filters merged
      - extra cannot override app_id → ValueError
      - empty app_id → ValueError

    scope_metadata:
      - returns {"app_id": ...}
      - optional fields included when non-empty
      - optional fields omitted when None
      - empty app_id → ValueError

  startup_policy.py (env-dependent):
    get_database_startup_policy:
      - unset → "best_effort"
      - "best_effort" → "best_effort"
      - "required" → "required"
      - invalid → DatabaseStartupPolicyError
"""
from __future__ import annotations

import hashlib

import pytest

from mozaiksai.core.runtime.persistence.naming import (
    collection_name_for,
    safe_identifier,
    scope_filter_for,
    scope_metadata,
    short_stable_hash,
)
from mozaiksai.core.runtime.persistence.startup_policy import (
    DatabaseStartupPolicyError,
    get_database_startup_policy,
)

# ---------------------------------------------------------------------------
# 1. safe_identifier
# ---------------------------------------------------------------------------

class TestSafeIdentifier:
    def test_plain_lowercase_unchanged(self):
        assert safe_identifier("wallet") == "wallet"

    def test_uppercase_lowercased(self):
        assert safe_identifier("Wallet") == "wallet"

    def test_numbers_preserved(self):
        assert safe_identifier("module123") == "module123"

    def test_special_chars_replaced(self):
        result = safe_identifier("my-module!")
        assert result == "my_module"

    def test_consecutive_underscores_collapsed(self):
        result = safe_identifier("a__b")
        assert result == "a_b"

    def test_leading_trailing_underscores_stripped(self):
        result = safe_identifier("_hello_")
        assert result == "hello"

    def test_empty_string_returns_default(self):
        assert safe_identifier("") == "item"

    def test_none_returns_default(self):
        assert safe_identifier(None) == "item"

    def test_custom_default(self):
        assert safe_identifier("", default="module") == "module"

    def test_result_truncated_to_max_length(self):
        long_value = "a" * 100
        result = safe_identifier(long_value, max_length=10)
        assert len(result) <= 10

    def test_unicode_normalized_to_ascii(self):
        # ñ → n (NFKD + ASCII encoding)
        result = safe_identifier("café")
        assert result == "cafe"

    def test_spaces_replaced(self):
        result = safe_identifier("my module name")
        assert result == "my_module_name"


# ---------------------------------------------------------------------------
# 2. short_stable_hash
# ---------------------------------------------------------------------------

class TestShortStableHash:
    def test_returns_string_of_specified_length(self):
        result = short_stable_hash("test")
        assert isinstance(result, str)
        assert len(result) == 8  # default _HASH_LENGTH

    def test_custom_length(self):
        result = short_stable_hash("test", length=4)
        assert len(result) == 4

    def test_deterministic(self):
        assert short_stable_hash("app-123") == short_stable_hash("app-123")

    def test_different_inputs_different_hashes(self):
        assert short_stable_hash("app-1") != short_stable_hash("app-2")

    def test_empty_input_raises(self):
        with pytest.raises(ValueError):
            short_stable_hash("")

    def test_matches_sha256_prefix(self):
        value = "myapp"
        expected = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        assert short_stable_hash(value) == expected


# ---------------------------------------------------------------------------
# 3. collection_name_for
# ---------------------------------------------------------------------------

class TestCollectionNameFor:
    def test_builds_deterministic_name(self):
        name1 = collection_name_for(app_id="app-1", module_id="wallet", entity_name="transaction")
        name2 = collection_name_for(app_id="app-1", module_id="wallet", entity_name="transaction")
        assert name1 == name2

    def test_name_contains_module_segment(self):
        name = collection_name_for(app_id="app-1", module_id="wallet", entity_name="balance")
        assert "wallet" in name

    def test_name_contains_entity_segment(self):
        name = collection_name_for(app_id="app-1", module_id="wallet", entity_name="balance")
        assert "balance" in name

    def test_special_chars_in_module_sanitized(self):
        name = collection_name_for(app_id="app-1", module_id="my-module!", entity_name="item")
        assert "my_module" in name

    def test_missing_app_id_raises(self):
        with pytest.raises(ValueError, match="app_id"):
            collection_name_for(app_id="", module_id="wallet", entity_name="balance")

    def test_missing_module_id_raises(self):
        with pytest.raises(ValueError, match="module_id"):
            collection_name_for(app_id="app-1", module_id="", entity_name="balance")

    def test_missing_entity_name_raises(self):
        with pytest.raises(ValueError, match="entity_name"):
            collection_name_for(app_id="app-1", module_id="wallet", entity_name="")

    def test_long_name_truncated(self):
        long_module = "m" * 60
        long_entity = "e" * 60
        name = collection_name_for(app_id="app-1", module_id=long_module, entity_name=long_entity)
        assert len(name) <= 120

    def test_custom_app_slug_used(self):
        name = collection_name_for(
            app_id="app-1", module_id="wallet", entity_name="tx", app_slug="myapp"
        )
        assert "myapp" in name


# ---------------------------------------------------------------------------
# 4. scope_filter_for
# ---------------------------------------------------------------------------

class TestScopeFilterFor:
    def test_returns_app_id_filter(self):
        result = scope_filter_for("app-1")
        assert result == {"app_id": "app-1"}

    def test_extra_filters_merged(self):
        result = scope_filter_for("app-1", extra={"status": "active"})
        assert result["app_id"] == "app-1"
        assert result["status"] == "active"

    def test_extra_cannot_override_app_id(self):
        with pytest.raises(ValueError, match="app_id"):
            scope_filter_for("app-1", extra={"app_id": "other"})

    def test_empty_app_id_raises(self):
        with pytest.raises(ValueError, match="app_id"):
            scope_filter_for("")

    def test_whitespace_app_id_raises(self):
        with pytest.raises(ValueError, match="app_id"):
            scope_filter_for("   ")

    def test_none_extra_treated_as_empty(self):
        result = scope_filter_for("app-1", extra=None)
        assert result == {"app_id": "app-1"}


# ---------------------------------------------------------------------------
# 5. scope_metadata
# ---------------------------------------------------------------------------

class TestScopeMetadata:
    def test_returns_app_id_only(self):
        result = scope_metadata("app-1")
        assert result == {"app_id": "app-1"}

    def test_tenant_id_included_when_set(self):
        result = scope_metadata("app-1", tenant_id="t-1")
        assert result["tenant_id"] == "t-1"

    def test_workspace_id_included_when_set(self):
        result = scope_metadata("app-1", workspace_id="ws-1")
        assert result["workspace_id"] == "ws-1"

    def test_user_id_included_when_set(self):
        result = scope_metadata("app-1", user_id="u-1")
        assert result["user_id"] == "u-1"

    def test_none_optional_fields_omitted(self):
        result = scope_metadata("app-1", tenant_id=None, workspace_id=None)
        assert "tenant_id" not in result
        assert "workspace_id" not in result

    def test_empty_optional_fields_omitted(self):
        result = scope_metadata("app-1", tenant_id="", workspace_id="   ")
        assert "tenant_id" not in result
        assert "workspace_id" not in result

    def test_empty_app_id_raises(self):
        with pytest.raises(ValueError, match="app_id"):
            scope_metadata("")


# ---------------------------------------------------------------------------
# 6. get_database_startup_policy (env-dependent)
# ---------------------------------------------------------------------------

class TestGetDatabaseStartupPolicy:
    def test_unset_returns_best_effort(self, monkeypatch):
        monkeypatch.delenv("MOZAIKS_DATABASE_STARTUP_POLICY", raising=False)
        assert get_database_startup_policy() == "best_effort"

    def test_best_effort_accepted(self, monkeypatch):
        monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
        assert get_database_startup_policy() == "best_effort"

    def test_required_accepted(self, monkeypatch):
        monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "required")
        assert get_database_startup_policy() == "required"

    def test_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "always")
        with pytest.raises(DatabaseStartupPolicyError):
            get_database_startup_policy()

    def test_whitespace_trimmed(self, monkeypatch):
        monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "  required  ")
        assert get_database_startup_policy() == "required"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "BEST_EFFORT")
        assert get_database_startup_policy() == "best_effort"
