"""
Connector service pure helper unit tests.

Covers:
  _normalize_service:
    - same contract as connector_request.normalize_service
    - None → "", whitespace stripped, lowercased, spaces → underscores

  _display_service:
    - underscore-separated → Title Case
    - single word → capitalized

  _normalize_required_fields (connector_service version):
    - None → []
    - string input → [] (Sequence but str is excluded)
    - empty list → []
    - valid field → normalized entry
    - field missing name → skipped
    - non-dict field → skipped
    - secret type → type="secret", frontend_safe=False
    - required defaults to True

  _classify_connector_status:
    - None → {exists: False, status: "missing", connector: None, days_until_expiry: None}
    - non-dict → same as None
    - status="revoked" → classified="revoked"
    - no secret_available → "metadata_only"
    - secret_available, expires_at in past → "expired"
    - secret_available, expires_at within 7 days → "expiring"
    - secret_available, expires_at > 7 days → "active"
    - secret_available, no expires_at → "active"
    - invalid expires_at string → treated as None
    - days_until_expiry is 0 when expires_at is now (not negative)
    - exists=True for all real record cases
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mozaiksai.core.workflow.generator_support.connector_service import (
    _classify_connector_status,
    _display_service,
    _normalize_required_fields,
    _normalize_service,
)

# ---------------------------------------------------------------------------
# 1. _normalize_service
# ---------------------------------------------------------------------------

class TestNormalizeService:
    def test_none_returns_empty(self):
        assert _normalize_service(None) == ""

    def test_empty_returns_empty(self):
        assert _normalize_service("") == ""

    def test_whitespace_stripped(self):
        assert _normalize_service("  stripe  ") == "stripe"

    def test_lowercased(self):
        assert _normalize_service("OpenAI") == "openai"

    def test_spaces_to_underscores(self):
        assert _normalize_service("my service") == "my_service"


# ---------------------------------------------------------------------------
# 2. _display_service
# ---------------------------------------------------------------------------

class TestDisplayService:
    def test_underscore_to_title(self):
        assert _display_service("stripe_billing") == "Stripe Billing"

    def test_single_word(self):
        assert _display_service("github") == "Github"

    def test_empty(self):
        assert _display_service("") == ""


# ---------------------------------------------------------------------------
# 3. _normalize_required_fields (connector_service version)
# ---------------------------------------------------------------------------

class TestNormalizeRequiredFields:
    def test_none_returns_empty(self):
        assert _normalize_required_fields(None) == []

    def test_string_input_returns_empty(self):
        # str is a Sequence but excluded
        assert _normalize_required_fields("api_key") == []  # type: ignore[arg-type]

    def test_empty_list_returns_empty(self):
        assert _normalize_required_fields([]) == []

    def test_valid_field_normalized(self):
        result = _normalize_required_fields([{"name": "base_url", "type": "text"}])
        assert len(result) == 1
        assert result[0]["name"] == "base_url"
        assert result[0]["type"] == "text"

    def test_field_missing_name_skipped(self):
        result = _normalize_required_fields([{"type": "text"}])
        assert result == []

    def test_non_dict_field_skipped(self):
        result = _normalize_required_fields(["not_a_dict", {"name": "valid"}])
        assert len(result) == 1
        assert result[0]["name"] == "valid"

    def test_secret_type_sets_frontend_safe_false(self):
        result = _normalize_required_fields([{"name": "key", "type": "secret"}])
        assert result[0]["type"] == "secret"
        assert result[0]["frontend_safe"] is False

    def test_api_key_type_treated_as_secret(self):
        result = _normalize_required_fields([{"name": "key", "type": "api_key"}])
        assert result[0]["type"] == "secret"

    def test_secret_flag_forces_secret_type(self):
        result = _normalize_required_fields([{"name": "key", "type": "text", "secret": True}])
        assert result[0]["type"] == "secret"

    def test_required_defaults_true(self):
        result = _normalize_required_fields([{"name": "x"}])
        assert result[0]["required"] is True

    def test_label_generated_from_name(self):
        result = _normalize_required_fields([{"name": "api_endpoint"}])
        assert result[0]["label"] == "Api Endpoint"


# ---------------------------------------------------------------------------
# 4. _classify_connector_status
# ---------------------------------------------------------------------------

def _future_iso(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _past_iso(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


class TestClassifyConnectorStatus:
    def test_none_returns_missing(self):
        result = _classify_connector_status(None)
        assert result["exists"] is False
        assert result["status"] == "missing"
        assert result["connector"] is None
        assert result["days_until_expiry"] is None

    def test_non_dict_returns_missing(self):
        result = _classify_connector_status("bad")  # type: ignore[arg-type]
        assert result["status"] == "missing"

    def test_revoked_status(self):
        record = {"status": "revoked", "secret_available": True}
        result = _classify_connector_status(record)
        assert result["status"] == "revoked"
        assert result["exists"] is True

    def test_no_secret_available_gives_metadata_only(self):
        record = {"status": "active", "secret_available": False}
        result = _classify_connector_status(record)
        assert result["status"] == "metadata_only"

    def test_secret_available_no_expiry_gives_active(self):
        record = {"secret_available": True}
        result = _classify_connector_status(record)
        assert result["status"] == "active"

    def test_secret_available_future_expiry_gives_active(self):
        record = {"secret_available": True, "expires_at": _future_iso(30)}
        result = _classify_connector_status(record)
        assert result["status"] == "active"
        assert result["days_until_expiry"] >= 29

    def test_secret_available_expiry_within_7_days_gives_expiring(self):
        record = {"secret_available": True, "expires_at": _future_iso(5)}
        result = _classify_connector_status(record)
        assert result["status"] == "expiring"
        assert result["days_until_expiry"] <= 7

    def test_secret_available_past_expiry_gives_expired(self):
        record = {"secret_available": True, "expires_at": _past_iso(1)}
        result = _classify_connector_status(record)
        assert result["status"] == "expired"

    def test_invalid_expires_at_treated_as_none(self):
        record = {"secret_available": True, "expires_at": "not-a-date"}
        result = _classify_connector_status(record)
        # No valid expiry → active
        assert result["status"] == "active"
        assert result["days_until_expiry"] is None

    def test_exists_true_for_real_records(self):
        record = {"secret_available": True}
        result = _classify_connector_status(record)
        assert result["exists"] is True

    def test_connector_field_set_to_record(self):
        record = {"secret_available": True, "service": "stripe"}
        result = _classify_connector_status(record)
        assert result["connector"] is record
