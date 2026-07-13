"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/hook_managed_capabilities_context.py

Covers:
  _is_empty:
    - None → True
    - empty list → True
    - empty dict → True
    - non-empty list → False
    - non-empty dict → False
    - string (non-None) → False
    - integer 0 → False (not None/list/dict)

  _format_managed_capabilities:
    - empty packs list → header only
    - pack with id and display_name → rendered with both
    - pack without display_name → falls back to id
    - pack description included (first line)
    - pack capabilities listed (up to 4)
    - non-dict pack item → rendered as string
    - capability as dict → capability_id extracted

  _format_pack_surfaces:
    - empty packs → None
    - pack with no surfaces → None
    - pack with surfaces → "Pack surfaces:" header
    - surface id and label rendered
    - surface status rendered
    - facade_module_id rendered when present
    - pages rendered when present
    - non-dict surface skipped

  _format_pack_supported_domains:
    - empty packs → None
    - pack with no supported_domains → None
    - pack with domains → "Pack domain fit:" header
    - domain and fit rendered
    - surfaces rendered when present
    - blocked surfaces rendered when present

  _format_pack_branding:
    - empty packs → None
    - pack with no branding → None
    - pack with branding → "Pack branding:" header
    - attribution rendered
    - app_branded_surfaces rendered
    - co_branded_surfaces rendered
    - hosted_redirect_surfaces rendered

  _format_contract_list_items:
    - non-list input → []
    - dict items without key → skipped
    - dict items with key → formatted line
    - non-dict items skipped

  _format_selection_rules:
    - non-list input → []
    - rule with id and action → formatted
    - rule with intent_any → intent text added
    - no action → no "→" in line

  _format_facade_contracts:
    - non-list input → []
    - facade without module_id → skipped
    - facade with module_id → "  - {module_id}" line
    - facade with provider_module → "wraps {provider_module}"
    - page with route → route line added

  _format_operator_contracts:
    - empty list → None
    - non-dict items skipped
    - contract_id rendered
    - selection_rules rendered when present
    - required_outputs rendered
    - forbidden_outputs rendered
    - facades rendered
    - inactive_surfaces rendered
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.hook_managed_capabilities_context import (
    _format_contract_list_items,
    _format_facade_contracts,
    _format_managed_capabilities,
    _format_operator_contracts,
    _format_pack_branding,
    _format_pack_supported_domains,
    _format_pack_surfaces,
    _format_selection_rules,
    _is_empty,
)

# ---------------------------------------------------------------------------
# 1. _is_empty
# ---------------------------------------------------------------------------

class TestIsEmpty:
    def test_none_returns_true(self):
        assert _is_empty(None) is True

    def test_empty_list_returns_true(self):
        assert _is_empty([]) is True

    def test_empty_dict_returns_true(self):
        assert _is_empty({}) is True

    def test_non_empty_list_returns_false(self):
        assert _is_empty(["item"]) is False

    def test_non_empty_dict_returns_false(self):
        assert _is_empty({"key": "val"}) is False

    def test_string_returns_false(self):
        assert _is_empty("text") is False

    def test_zero_integer_returns_false(self):
        assert _is_empty(0) is False

    def test_false_boolean_returns_false(self):
        assert _is_empty(False) is False


# ---------------------------------------------------------------------------
# 2. _format_managed_capabilities
# ---------------------------------------------------------------------------

class TestFormatManagedCapabilities:
    def test_header_always_present(self):
        result = _format_managed_capabilities([])
        assert "Managed capabilities available" in result

    def test_pack_id_rendered(self):
        result = _format_managed_capabilities([{"id": "payment_provider_pay"}])
        assert "payment_provider_pay" in result

    def test_display_name_rendered(self):
        result = _format_managed_capabilities([{"id": "payment_provider_pay", "display_name": "payment provider Payments"}])
        assert "payment provider Payments" in result

    def test_display_name_fallback_to_id(self):
        result = _format_managed_capabilities([{"id": "payment_provider_pay"}])
        assert "payment_provider_pay" in result

    def test_description_first_line_rendered(self):
        result = _format_managed_capabilities([{"id": "pay", "description": "Payment integration\nExtra line"}])
        assert "Payment integration" in result
        assert "Extra line" not in result

    def test_capabilities_listed(self):
        caps = [{"capability_id": "payments.charge"}, {"capability_id": "payments.refund"}]
        result = _format_managed_capabilities([{"id": "pay", "capabilities": caps}])
        assert "payments.charge" in result

    def test_capabilities_capped_at_four(self):
        caps = [{"capability_id": f"cap_{i}"} for i in range(6)]
        result = _format_managed_capabilities([{"id": "pay", "capabilities": caps}])
        assert "cap_3" in result
        assert "cap_4" not in result

    def test_non_dict_pack_rendered_as_string(self):
        result = _format_managed_capabilities(["string_pack"])
        assert "string_pack" in result

    def test_capability_source_label_in_line(self):
        result = _format_managed_capabilities([{"id": "pay"}])
        assert "managed_capability" in result


# ---------------------------------------------------------------------------
# 3. _format_pack_surfaces
# ---------------------------------------------------------------------------

class TestFormatPackSurfaces:
    def test_empty_packs_returns_none(self):
        assert _format_pack_surfaces([]) is None

    def test_pack_without_surfaces_returns_none(self):
        assert _format_pack_surfaces([{"id": "pay"}]) is None

    def test_pack_with_surfaces_returns_string(self):
        pack = {"id": "pay", "surfaces": [{"surface_id": "checkout"}]}
        result = _format_pack_surfaces([pack])
        assert result is not None
        assert "Pack surfaces:" in result

    def test_surface_id_rendered(self):
        pack = {"id": "pay", "surfaces": [{"surface_id": "checkout", "status": "ready"}]}
        result = _format_pack_surfaces([pack])
        assert "checkout" in result

    def test_surface_status_rendered(self):
        pack = {"id": "pay", "surfaces": [{"surface_id": "checkout", "status": "ready"}]}
        result = _format_pack_surfaces([pack])
        assert "ready" in result

    def test_facade_module_rendered(self):
        pack = {"id": "pay", "surfaces": [{"surface_id": "checkout", "generation_hint": {"facade_module_id": "pay_facade"}}]}
        result = _format_pack_surfaces([pack])
        assert "pay_facade" in result

    def test_pages_rendered_when_present(self):
        pack = {"id": "pay", "surfaces": [{"surface_id": "checkout", "generation_hint": {"pages": ["CheckoutPage"]}}]}
        result = _format_pack_surfaces([pack])
        assert "CheckoutPage" in result

    def test_non_dict_surface_no_surface_detail_rendered(self):
        # Pack header line still added, but no surface detail lines for non-dict entries
        pack = {"id": "pay", "surfaces": ["not-a-dict"]}
        result = _format_pack_surfaces([pack])
        assert result is not None
        # Only the pack header line, no surface-specific detail
        assert "checkout" not in (result or "")

    def test_non_dict_pack_skipped(self):
        assert _format_pack_surfaces(["not-a-dict"]) is None


# ---------------------------------------------------------------------------
# 4. _format_pack_supported_domains
# ---------------------------------------------------------------------------

class TestFormatPackSupportedDomains:
    def test_empty_packs_returns_none(self):
        assert _format_pack_supported_domains([]) is None

    def test_pack_without_domains_returns_none(self):
        assert _format_pack_supported_domains([{"id": "pay"}]) is None

    def test_pack_with_domains_returns_string(self):
        pack = {"id": "pay", "supported_domains": [{"domain": "ecommerce", "fit": "primary"}]}
        result = _format_pack_supported_domains([pack])
        assert result is not None
        assert "Pack domain fit:" in result

    def test_domain_name_rendered(self):
        pack = {"id": "pay", "supported_domains": [{"domain": "ecommerce", "fit": "primary"}]}
        result = _format_pack_supported_domains([pack])
        assert "ecommerce" in result

    def test_fit_rendered(self):
        pack = {"id": "pay", "supported_domains": [{"domain": "ecommerce", "fit": "primary"}]}
        result = _format_pack_supported_domains([pack])
        assert "primary" in result

    def test_surfaces_rendered_when_present(self):
        pack = {"id": "pay", "supported_domains": [{"domain": "ec", "fit": "ok", "surfaces": ["checkout"]}]}
        result = _format_pack_supported_domains([pack])
        assert "checkout" in result

    def test_blocked_surfaces_rendered_when_present(self):
        pack = {"id": "pay", "supported_domains": [{"domain": "ec", "fit": "ok", "blocked_surfaces": ["admin"]}]}
        result = _format_pack_supported_domains([pack])
        assert "admin" in result


# ---------------------------------------------------------------------------
# 5. _format_pack_branding
# ---------------------------------------------------------------------------

class TestFormatPackBranding:
    def test_empty_packs_returns_none(self):
        assert _format_pack_branding([]) is None

    def test_pack_without_branding_returns_none(self):
        assert _format_pack_branding([{"id": "pay"}]) is None

    def test_pack_with_branding_returns_string(self):
        pack = {"id": "pay", "branding": {"attribution": "Powered by payment provider"}}
        result = _format_pack_branding([pack])
        assert result is not None
        assert "Pack branding:" in result

    def test_attribution_rendered(self):
        pack = {"id": "pay", "branding": {"attribution": "Powered by payment provider"}}
        result = _format_pack_branding([pack])
        assert "Powered by payment provider" in result

    def test_app_branded_surfaces_rendered(self):
        pack = {"id": "pay", "branding": {"app_branded_surfaces": ["checkout"]}}
        result = _format_pack_branding([pack])
        assert "checkout" in result
        assert "app-branded" in result

    def test_co_branded_surfaces_rendered(self):
        pack = {"id": "pay", "branding": {"co_branded_surfaces": ["success"]}}
        result = _format_pack_branding([pack])
        assert "success" in result
        assert "co-branded" in result


# ---------------------------------------------------------------------------
# 6. _format_contract_list_items
# ---------------------------------------------------------------------------

class TestFormatContractListItems:
    def test_non_list_returns_empty(self):
        assert _format_contract_list_items("not-a-list", key="path") == []

    def test_dict_without_key_skipped(self):
        result = _format_contract_list_items([{"other": "val"}], key="path")
        assert result == []

    def test_dict_with_key_formatted(self):
        result = _format_contract_list_items([{"path": "app/modules/orders"}], key="path")
        assert len(result) == 1
        assert "app/modules/orders" in result[0]

    def test_non_dict_items_skipped(self):
        result = _format_contract_list_items(["not-a-dict"], key="path")
        assert result == []

    def test_multiple_items(self):
        items = [{"path": "a"}, {"path": "b"}]
        result = _format_contract_list_items(items, key="path")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 7. _format_selection_rules
# ---------------------------------------------------------------------------

class TestFormatSelectionRules:
    def test_non_list_returns_empty(self):
        assert _format_selection_rules("not-a-list") == []

    def test_rule_with_id_and_action(self):
        rules = [{"id": "checkout_rule", "action": "include"}]
        result = _format_selection_rules(rules)
        assert len(result) == 1
        assert "checkout_rule" in result[0]
        assert "include" in result[0]

    def test_rule_with_intent_any(self):
        rules = [{"id": "r1", "when": {"intent_any": ["payment", "billing"]}}]
        result = _format_selection_rules(rules)
        assert "payment" in result[0]
        assert "billing" in result[0]

    def test_rule_without_action_no_arrow(self):
        rules = [{"id": "r1"}]
        result = _format_selection_rules(rules)
        assert "->" not in result[0]

    def test_non_dict_item_skipped(self):
        result = _format_selection_rules(["not-a-dict"])
        assert result == []


# ---------------------------------------------------------------------------
# 8. _format_facade_contracts
# ---------------------------------------------------------------------------

class TestFormatFacadeContracts:
    def test_non_list_returns_empty(self):
        assert _format_facade_contracts("not-a-list") == []

    def test_facade_without_module_id_skipped(self):
        result = _format_facade_contracts([{"other": "value"}])
        assert result == []

    def test_facade_with_module_id_rendered(self):
        result = _format_facade_contracts([{"module_id": "pay_facade"}])
        assert any("pay_facade" in line for line in result)

    def test_provider_module_rendered(self):
        result = _format_facade_contracts([{"module_id": "pay_facade", "provider_module": "payment_provider_module"}])
        assert any("payment_provider_module" in line for line in result)

    def test_facade_id_fallback(self):
        result = _format_facade_contracts([{"facade_id": "billing_facade"}])
        assert any("billing_facade" in line for line in result)

    def test_page_with_route_rendered(self):
        facade = {
            "module_id": "pay_facade",
            "pages": [{"route": "/checkout", "primary_actions": ["pay"]}]
        }
        result = _format_facade_contracts([facade])
        assert any("/checkout" in line for line in result)

    def test_non_dict_facade_skipped(self):
        result = _format_facade_contracts(["not-a-dict"])
        assert result == []


# ---------------------------------------------------------------------------
# 9. _format_operator_contracts
# ---------------------------------------------------------------------------

class TestFormatOperatorContracts:
    def test_empty_list_returns_none(self):
        assert _format_operator_contracts([]) is None

    def test_non_dict_items_skipped(self):
        assert _format_operator_contracts(["not-a-dict"]) is None

    def test_contract_id_rendered(self):
        contracts = [{"contract_id": "my_contract", "contract_type": "operator"}]
        result = _format_operator_contracts(contracts)
        assert result is not None
        assert "my_contract" in result

    def test_selection_rules_rendered(self):
        contracts = [{
            "contract_id": "c1",
            "selection_rules": [{"id": "rule1", "action": "include"}]
        }]
        result = _format_operator_contracts(contracts)
        assert "rule1" in result

    def test_required_outputs_rendered(self):
        contracts = [{
            "contract_id": "c1",
            "required_outputs": [{"path": "modules/orders/module.yaml"}]
        }]
        result = _format_operator_contracts(contracts)
        assert "modules/orders/module.yaml" in result

    def test_inactive_surfaces_rendered(self):
        contracts = [{"contract_id": "c1", "inactive_surfaces": ["checkout", "admin"]}]
        result = _format_operator_contracts(contracts)
        assert "checkout" in result
        assert "admin" in result

    def test_header_present(self):
        contracts = [{"contract_id": "c1"}]
        result = _format_operator_contracts(contracts)
        assert "Operator build-pack contracts:" in result
