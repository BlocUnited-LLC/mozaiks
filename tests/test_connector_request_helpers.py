"""
Connector request pure helper unit tests.

Covers:
  normalize_service:
    - None → ""
    - empty string → ""
    - whitespace stripped
    - lowercased
    - spaces replaced with underscores
    - already normalized → unchanged

  display_service:
    - underscore-separated string → title-cased words
    - single word → capitalized
    - already display format → consistent result

  _normalize_required_fields:
    - no fields, kind="api_key" → default [api_key secret field]
    - no fields, kind="oauth" → default [oauth_secret field]
    - no fields, kind="token" → default [token field with name "token"]...
      actually: kind in {"api_key","key","secret"} → "api_key", else f"{kind}_secret"
    - fields list with valid field → normalized entry
    - field missing name → skipped
    - field with type="secret" → is_secret=True, type="secret", frontend_safe=False
    - field with type="password" → is_secret=True
    - field with type="api_key" → is_secret=True
    - field with secret=True flag → is_secret=True
    - field without label → label generated from name
    - field with options list → options included
    - non-dict item in fields → skipped
    - required defaults to True

  _split_required_fields:
    - empty list → ([], [])
    - secret field → in secret_fields
    - api_key field → in secret_fields
    - password field → in secret_fields
    - text field with frontend_safe=True → in non_secret_fields
    - field with frontend_safe=False → not in non_secret_fields (skipped)
    - field with frontend_safe not set → defaults included

  _iter_dicts:
    - non-list input → yields nothing
    - list with dicts and non-dicts → yields only dicts
    - empty list → yields nothing
    - list of all dicts → yields all

  dedupe_integration_needs:
    - empty list → []
    - single need → normalized and returned
    - non-dict need → skipped
    - need with no service → skipped
    - two needs with same service → merged into one
    - duplicate optional=True both → optional=True in output
    - duplicate one optional=True, one optional=False → optional=False
    - required_at priority: build_time < validation_time < runtime < deployment
    - purpose merged from second if first has no purpose
    - required_by accumulated as list
    - output sorted by display_name
"""
from __future__ import annotations

from mozaiksai.core.workflow.generator_support.connector_request import (
    _extract_needs_from_plan,
    _iter_dicts,
    _normalize_required_fields,
    _split_required_fields,
    dedupe_integration_needs,
    display_service,
    normalize_service,
)

# ---------------------------------------------------------------------------
# 1. normalize_service
# ---------------------------------------------------------------------------

class TestNormalizeService:
    def test_none_returns_empty(self):
        assert normalize_service(None) == ""

    def test_empty_returns_empty(self):
        assert normalize_service("") == ""

    def test_whitespace_stripped(self):
        assert normalize_service("  payment_provider  ") == "payment_provider"

    def test_lowercased(self):
        assert normalize_service("payment provider") == "payment_provider"

    def test_spaces_to_underscores(self):
        assert normalize_service("my service") == "my_service"

    def test_already_normalized(self):
        assert normalize_service("payment_provider_billing") == "payment_provider_billing"

    def test_combined(self):
        assert normalize_service("  My Service  ") == "my_service"


# ---------------------------------------------------------------------------
# 2. display_service
# ---------------------------------------------------------------------------

class TestDisplayService:
    def test_underscored_to_title(self):
        assert display_service("my_service") == "My Service"

    def test_single_word(self):
        assert display_service("payment_provider") == "Payment Provider"

    def test_already_normalized_becomes_title(self):
        assert display_service("payment_provider_billing") == "Payment Provider Billing"

    def test_empty_service(self):
        assert display_service("") == ""


# ---------------------------------------------------------------------------
# 3. _normalize_required_fields
# ---------------------------------------------------------------------------

class TestNormalizeRequiredFields:
    def test_no_fields_api_key_kind_gives_default(self):
        result = _normalize_required_fields({"kind": "api_key"})
        assert len(result) == 1
        assert result[0]["name"] == "api_key"
        assert result[0]["type"] == "secret"
        assert result[0]["required"] is True
        assert result[0]["frontend_safe"] is False

    def test_no_fields_key_kind_gives_api_key(self):
        result = _normalize_required_fields({"kind": "key"})
        assert result[0]["name"] == "api_key"

    def test_no_fields_secret_kind_gives_api_key(self):
        result = _normalize_required_fields({"kind": "secret"})
        assert result[0]["name"] == "api_key"

    def test_no_fields_oauth_kind_gives_oauth_secret(self):
        result = _normalize_required_fields({"kind": "oauth"})
        assert result[0]["name"] == "oauth_secret"

    def test_empty_required_fields_uses_default(self):
        result = _normalize_required_fields({"kind": "api_key", "required_fields": []})
        assert result[0]["name"] == "api_key"

    def test_fields_with_name_normalized(self):
        raw = {"required_fields": [{"name": "endpoint_url", "type": "text"}]}
        result = _normalize_required_fields(raw)
        assert len(result) == 1
        assert result[0]["name"] == "endpoint_url"
        assert result[0]["type"] == "text"

    def test_field_missing_name_skipped(self):
        raw = {"required_fields": [{"type": "text"}]}
        result = _normalize_required_fields(raw)
        assert len(result) == 1  # Falls back to default
        assert result[0]["name"] == "api_key"  # Default

    def test_secret_type_is_secret_and_not_frontend_safe(self):
        raw = {"required_fields": [{"name": "key", "type": "secret"}]}
        result = _normalize_required_fields(raw)
        assert result[0]["type"] == "secret"
        assert result[0]["frontend_safe"] is False

    def test_password_type_treated_as_secret(self):
        raw = {"required_fields": [{"name": "pw", "type": "password"}]}
        result = _normalize_required_fields(raw)
        assert result[0]["type"] == "secret"
        assert result[0]["frontend_safe"] is False

    def test_api_key_type_treated_as_secret(self):
        raw = {"required_fields": [{"name": "key", "type": "api_key"}]}
        result = _normalize_required_fields(raw)
        assert result[0]["type"] == "secret"

    def test_secret_flag_forces_secret_type(self):
        raw = {"required_fields": [{"name": "key", "type": "text", "secret": True}]}
        result = _normalize_required_fields(raw)
        assert result[0]["type"] == "secret"

    def test_label_generated_from_name_if_absent(self):
        raw = {"required_fields": [{"name": "api_endpoint"}]}
        result = _normalize_required_fields(raw)
        assert result[0]["label"] == "Api Endpoint"

    def test_explicit_label_preserved(self):
        raw = {"required_fields": [{"name": "x", "label": "Custom Label"}]}
        result = _normalize_required_fields(raw)
        assert result[0]["label"] == "Custom Label"

    def test_options_list_included(self):
        raw = {"required_fields": [{"name": "region", "options": ["us", "eu"]}]}
        result = _normalize_required_fields(raw)
        assert result[0]["options"] == ["us", "eu"]

    def test_non_dict_field_skipped(self):
        raw = {"required_fields": ["not_a_dict", {"name": "valid"}]}
        result = _normalize_required_fields(raw)
        assert len(result) == 1
        assert result[0]["name"] == "valid"

    def test_required_defaults_true(self):
        raw = {"required_fields": [{"name": "key"}]}
        result = _normalize_required_fields(raw)
        assert result[0]["required"] is True

    def test_required_can_be_false(self):
        raw = {"required_fields": [{"name": "optional_key", "required": False}]}
        result = _normalize_required_fields(raw)
        assert result[0]["required"] is False


# ---------------------------------------------------------------------------
# 4. _split_required_fields
# ---------------------------------------------------------------------------

class TestSplitRequiredFields:
    def test_empty_list(self):
        secret, non_secret = _split_required_fields([])
        assert secret == []
        assert non_secret == []

    def test_secret_type_goes_to_secret_fields(self):
        fields = [{"name": "key", "type": "secret", "frontend_safe": False}]
        secret, non_secret = _split_required_fields(fields)
        assert len(secret) == 1
        assert non_secret == []

    def test_api_key_type_goes_to_secret_fields(self):
        fields = [{"name": "k", "type": "api_key", "frontend_safe": False}]
        secret, non_secret = _split_required_fields(fields)
        assert len(secret) == 1

    def test_password_type_goes_to_secret_fields(self):
        fields = [{"name": "pw", "type": "password"}]
        secret, non_secret = _split_required_fields(fields)
        assert len(secret) == 1

    def test_text_field_frontend_safe_goes_to_non_secret(self):
        fields = [{"name": "url", "type": "text", "frontend_safe": True}]
        secret, non_secret = _split_required_fields(fields)
        assert len(non_secret) == 1
        assert len(secret) == 0

    def test_field_with_frontend_safe_false_not_in_non_secret(self):
        fields = [{"name": "url", "type": "text", "frontend_safe": False}]
        secret, non_secret = _split_required_fields(fields)
        assert len(non_secret) == 0
        assert len(secret) == 0  # Not a secret type either

    def test_mixed_fields_split_correctly(self):
        fields = [
            {"name": "api_key", "type": "secret"},
            {"name": "base_url", "type": "text", "frontend_safe": True},
        ]
        secret, non_secret = _split_required_fields(fields)
        assert len(secret) == 1
        assert len(non_secret) == 1


# ---------------------------------------------------------------------------
# 5. _iter_dicts
# ---------------------------------------------------------------------------

class TestIterDicts:
    def test_non_list_yields_nothing(self):
        assert list(_iter_dicts("not_list")) == []
        assert list(_iter_dicts(None)) == []
        assert list(_iter_dicts(42)) == []

    def test_empty_list_yields_nothing(self):
        assert list(_iter_dicts([])) == []

    def test_list_with_dicts_and_non_dicts(self):
        items = [{"a": 1}, "text", {"b": 2}, None, 3]
        result = list(_iter_dicts(items))
        assert result == [{"a": 1}, {"b": 2}]

    def test_all_dicts(self):
        items = [{"x": 1}, {"y": 2}]
        assert list(_iter_dicts(items)) == items

    def test_no_dicts_in_list(self):
        assert list(_iter_dicts(["a", "b", 3])) == []


# ---------------------------------------------------------------------------
# 6. _extract_needs_from_plan
# ---------------------------------------------------------------------------

class TestExtractNeedsFromPlan:
    def test_structured_capability_pack_required_integration_preserves_fields(self):
        plan = {
            "capability_packs": [
                {
                    "capability_pack_id": "mozaikspay",
                    "required_integrations": [
                        {
                            "service": "mozaikspay",
                            "provider": "mozaikspay",
                            "display_name": "MozaiksPay",
                            "kind": "api_key",
                            "purpose": "Connect generated app facades to hosted MozaiksPay.",
                            "required_at": "runtime",
                            "required_fields": [
                                {"name": "api_base", "type": "url", "frontend_safe": True},
                                {"name": "client_id", "type": "text", "frontend_safe": True},
                                {"name": "client_secret", "type": "secret", "frontend_safe": False},
                            ],
                        }
                    ],
                }
            ]
        }

        result = dedupe_integration_needs(_extract_needs_from_plan(plan))

        assert len(result) == 1
        need = result[0]
        assert need["service"] == "mozaikspay"
        assert need["provider"] == "mozaikspay"
        assert need["display_name"] == "MozaiksPay"
        assert need["required_at"] == "runtime"
        assert {field["name"] for field in need["required_fields"]} == {
            "api_base",
            "client_id",
            "client_secret",
        }
        client_secret = next(field for field in need["required_fields"] if field["name"] == "client_secret")
        assert client_secret["type"] == "secret"
        assert client_secret["frontend_safe"] is False
        assert need["required_by"] == [{"source": "capability_pack", "capability_pack_id": "mozaikspay"}]


# ---------------------------------------------------------------------------
# 7. dedupe_integration_needs
# ---------------------------------------------------------------------------

class TestDedupeIntegrationNeeds:
    def _need(self, service: str, **kwargs) -> dict:
        return {"service": service, "kind": "api_key", "optional": False, **kwargs}

    def test_empty_returns_empty(self):
        assert dedupe_integration_needs([]) == []

    def test_single_need_normalized(self):
        result = dedupe_integration_needs([self._need("payment_provider")])
        assert len(result) == 1
        assert result[0]["service"] == "payment_provider"

    def test_non_dict_need_skipped(self):
        result = dedupe_integration_needs(["bad", self._need("payment_provider")])
        assert len(result) == 1

    def test_need_with_no_service_skipped(self):
        result = dedupe_integration_needs([{"kind": "api_key"}])
        assert result == []

    def test_duplicate_service_merged(self):
        needs = [self._need("payment_provider"), self._need("payment_provider")]
        result = dedupe_integration_needs(needs)
        assert len(result) == 1

    def test_duplicate_service_prefers_more_complete_metadata_and_fields(self):
        needs = [
            self._need(
                "mozaikspay",
                required_fields=[{"name": "client_secret", "type": "secret"}],
            ),
            self._need(
                "mozaikspay",
                provider="mozaikspay",
                display_name="MozaiksPay",
                required_fields=[
                    {"name": "api_base", "type": "url", "frontend_safe": True},
                    {"name": "client_secret", "type": "secret", "frontend_safe": False},
                ],
            ),
        ]

        result = dedupe_integration_needs(needs)

        assert len(result) == 1
        need = result[0]
        assert need["provider"] == "mozaikspay"
        assert need["display_name"] == "MozaiksPay"
        assert {field["name"] for field in need["required_fields"]} == {"api_base", "client_secret"}

    def test_both_optional_stays_optional(self):
        needs = [
            self._need("payment_provider", optional=True),
            self._need("payment_provider", optional=True),
        ]
        result = dedupe_integration_needs(needs)
        assert result[0]["optional"] is True

    def test_one_not_optional_makes_required(self):
        needs = [
            self._need("payment_provider", optional=True),
            self._need("payment_provider", optional=False),
        ]
        result = dedupe_integration_needs(needs)
        assert result[0]["optional"] is False

    def test_build_time_takes_priority_over_runtime(self):
        needs = [
            self._need("payment_provider", required_at="runtime"),
            self._need("payment_provider", required_at="build_time"),
        ]
        result = dedupe_integration_needs(needs)
        assert result[0]["required_at"] == "build_time"

    def test_purpose_merged_if_first_empty(self):
        needs = [
            {**self._need("payment_provider"), "purpose": ""},
            {**self._need("payment_provider"), "purpose": "Needed for payments."},
        ]
        result = dedupe_integration_needs(needs)
        assert result[0]["purpose"] == "Needed for payments."

    def test_required_by_accumulated_as_list(self):
        needs = [
            {**self._need("payment_provider"), "required_by": {"source": "a"}},
            {**self._need("payment_provider"), "required_by": {"source": "b"}},
        ]
        result = dedupe_integration_needs(needs)
        assert len(result[0]["required_by"]) == 2

    def test_output_sorted_by_display_name(self):
        needs = [self._need("payment_provider"), self._need("openai"), self._need("github")]
        result = dedupe_integration_needs(needs)
        names = [r["service"] for r in result]
        assert names == sorted(names)
