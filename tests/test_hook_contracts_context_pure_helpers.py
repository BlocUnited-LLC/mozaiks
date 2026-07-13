"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/hook_contracts_context.py

Covers:
  _format_connector_inventory_block:
    - empty inventory → "none" for ready and required
    - ready services listed
    - required services listed
    - missing services section appears when present
    - known_but_unready section appears when present
    - display_names used when present
    - display_names fall back to title-cased service name
    - static enforcement lines always present
    - empty inventory object → all blank sections use "none"

  _context_get:
    - None context → default returned
    - dict context → value returned
    - dict context → missing key returns default
    - context with .get() method → value returned
    - context with .data dict → value returned
    - context with .get() method returning None → default returned
"""
from __future__ import annotations

from typing import Any

from factory_app.workflows.AppGenerator.tools.hook_contracts_context import (
    _context_get,
    _format_connector_inventory_block,
)

# ---------------------------------------------------------------------------
# 1. _format_connector_inventory_block
# ---------------------------------------------------------------------------

class TestFormatConnectorInventoryBlock:
    def test_empty_inventory_shows_none_for_ready(self):
        result = _format_connector_inventory_block({})
        assert "Ready connectors: none" in result

    def test_empty_inventory_shows_none_for_required(self):
        result = _format_connector_inventory_block({})
        assert "Required by current plan: none" in result

    def test_ready_services_listed(self):
        inventory = {"ready_services": ["payment_provider", "sendgrid"]}
        result = _format_connector_inventory_block(inventory)
        # display_service() title-cases the key: payment_provider → "Payment Provider"
        assert "payment provider" in result.lower()

    def test_required_services_listed(self):
        inventory = {"required_services": ["payment_provider"]}
        result = _format_connector_inventory_block(inventory)
        assert "payment provider" in result.lower()

    def test_missing_services_section_appears_when_present(self):
        inventory = {"missing_required_services": ["sendgrid"]}
        result = _format_connector_inventory_block(inventory)
        assert "Still missing" in result

    def test_missing_services_section_absent_when_empty(self):
        result = _format_connector_inventory_block({})
        assert "Still missing" not in result

    def test_known_unready_section_appears_when_present(self):
        inventory = {"known_but_unready_required_services": ["mailchimp"]}
        result = _format_connector_inventory_block(inventory)
        assert "not runtime-ready" in result

    def test_known_unready_section_absent_when_empty(self):
        result = _format_connector_inventory_block({})
        assert "not runtime-ready" not in result

    def test_display_name_used_when_present(self):
        inventory = {
            "ready_services": ["payment_provider"],
            "display_names": {"payment_provider": "payment provider Payments"},
        }
        result = _format_connector_inventory_block(inventory)
        assert "payment provider Payments" in result

    def test_display_name_fallback_title_case(self):
        # No display_name for "my_service" → title case "My Service"
        inventory = {"ready_services": ["my_service"]}
        result = _format_connector_inventory_block(inventory)
        assert "My Service" in result

    def test_static_no_api_key_table_line_present(self):
        result = _format_connector_inventory_block({})
        assert "Never invent a separate API-key table" in result

    def test_static_missing_connector_line_present(self):
        result = _format_connector_inventory_block({})
        assert "not ready here" in result

    def test_result_starts_with_header(self):
        result = _format_connector_inventory_block({})
        assert result.startswith("App Connector Inventory:")

    def test_multiple_ready_services_all_listed(self):
        inventory = {
            "ready_services": ["payment_provider", "sendgrid"],
            "display_names": {"payment_provider": "payment provider", "sendgrid": "SendGrid"},
        }
        result = _format_connector_inventory_block(inventory)
        assert "payment provider" in result
        assert "SendGrid" in result


# ---------------------------------------------------------------------------
# 2. _context_get
# ---------------------------------------------------------------------------

class TestContextGet:
    def test_none_context_returns_default(self):
        assert _context_get(None, "key") is None

    def test_none_context_returns_custom_default(self):
        assert _context_get(None, "key", "fallback") == "fallback"

    def test_dict_context_returns_value(self):
        ctx = {"app_id": "test123"}
        assert _context_get(ctx, "app_id") == "test123"

    def test_dict_context_missing_key_returns_default(self):
        ctx = {"app_id": "test123"}
        assert _context_get(ctx, "missing_key") is None

    def test_dict_context_missing_key_returns_custom_default(self):
        ctx = {}
        assert _context_get(ctx, "key", "default_val") == "default_val"

    def test_context_with_get_method(self):
        class FakeCtx:
            def get(self, key: str, default: Any = None) -> Any:
                if key == "app_id":
                    return "app-abc"
                return default

        assert _context_get(FakeCtx(), "app_id") == "app-abc"

    def test_context_with_get_method_missing_key(self):
        class FakeCtx:
            def get(self, key: str, default: Any = None) -> Any:
                return default

        assert _context_get(FakeCtx(), "missing", "fb") == "fb"

    def test_context_with_get_called_with_default_arg(self):
        # _context_get passes default to getter(key, default) — getter receives it
        received: dict = {}

        class FakeCtx:
            def get(self, key: str, default: Any = None) -> Any:
                received["default"] = default
                return default

        result = _context_get(FakeCtx(), "key", "fb")
        assert result == "fb"
        assert received["default"] == "fb"

    def test_context_with_data_dict(self):
        class FakeCtx:
            data = {"app_id": "data-abc"}

        assert _context_get(FakeCtx(), "app_id") == "data-abc"

    def test_context_with_data_dict_missing_key_returns_default(self):
        class FakeCtx:
            data: dict[str, Any] = {}

        assert _context_get(FakeCtx(), "missing", "fb") == "fb"
