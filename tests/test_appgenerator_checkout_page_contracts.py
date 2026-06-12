"""
AppGenerator checkout page contract tests.

Verifies that AppSchemaAgent guidance and file_contracts correctly encode the four
checkout-flow page archetypes and their escape-hatch rules.

Scope:
  1.  AppSchemaAgent guidance mentions wizard page type for checkout initiation.
  2.  AppSchemaAgent guidance mentions landing page type for static cancellation pages.
  3.  AppSchemaAgent guidance mentions record_list for order/payment history.
  4.  AppSchemaAgent guidance states custom_route_bundle required for query param + polling.
  5.  AppSchemaAgent guidance states redirect alone is not confirmation.
  6.  file_contracts page_bundle hard_constraints mention custom_route_bundle for
      query-param polling pages.
  7.  file_contracts page_bundle states post-redirect page must not confirm from redirect.
  8.  file_contracts page_bundle states api_endpoint has no query string support.
  9.  Neutral declarative fixture: checkout initiation page uses wizard + focused.
  10. Neutral declarative fixture: cancellation page uses landing + focused + no endpoint.
  11. Neutral declarative fixture: order history page uses record_list + workspace.
  12. Neutral custom_route_bundle fixture: post-redirect confirmation page.
  13. custom_route_bundle fixture requires route_manifest + component registration.
  14. No declarative page binds directly to an external payment provider endpoint.
  15. No provider-specific names appear in OSS guidance or tests.

All fixtures use neutral names: checkout_module, ecommerce_checkout, external_payment_provider.
No MozaiksPay, Stripe, wallet, billing, or proprietary hosted-product names in OSS tests.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

_WORKSPACE = Path(__file__).resolve().parents[1]
_AGENTS_YAML_PATH = _WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml"
_FILE_CONTRACTS_PATH = (
    _WORKSPACE / "factory_app" / "build_context" / "AppGenerator" / "file_contracts.yaml"
)

# Names that must never appear in OSS guidance or this test file.
_PROPRIETARY_NAMES = [
    "MozaiksPay",
    "mozaikspay",
    "Stripe",
    "stripe",
    "wallet",
    "hosted_billing",
    "hosted_usage",
    "hosted_entitlements",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agents_yaml_text() -> str:
    return _AGENTS_YAML_PATH.read_text(encoding="utf-8")


def _file_contracts_text() -> str:
    return _FILE_CONTRACTS_PATH.read_text(encoding="utf-8")


def _app_schema_agent_section(text: str) -> str:
    """Extract the AppSchemaAgent block from agents.yaml text."""
    start = text.find("- name: AppSchemaAgent")
    if start == -1:
        return text
    # Find the next top-level agent declaration after AppSchemaAgent
    next_agent = text.find("\n- name:", start + 1)
    return text[start:next_agent] if next_agent != -1 else text[start:]


def _page_bundle_section(text: str) -> str:
    """Extract the page_bundle task contract from file_contracts.yaml text."""
    start = text.find("  page_bundle:")
    if start == -1:
        return text
    next_key = text.find("\n  ", start + 1)
    return text[start:next_key] if next_key != -1 else text[start:]


# ---------------------------------------------------------------------------
# 1-5: AppSchemaAgent guidance content
# ---------------------------------------------------------------------------


class TestAppSchemaAgentCheckoutGuidance:
    """Verify AppSchemaAgent prompt encodes correct page archetypes for checkout flows."""

    def _section(self) -> str:
        return _app_schema_agent_section(_agents_yaml_text())

    def test_wizard_mentioned_for_checkout_initiation(self):
        """AppSchemaAgent guidance must associate wizard with checkout flows."""
        section = self._section()
        # wizard archetype already exists; confirm checkout context is adjacent
        assert "wizard" in section, "AppSchemaAgent must reference wizard page type"
        # Confirm checkout context is present near wizard / external-redirect section
        assert "checkout" in section.lower(), (
            "AppSchemaAgent guidance must mention checkout in the context of wizard/focused flow"
        )

    def test_focused_shell_mode_for_checkout_page(self):
        """Checkout initiation page must use shell_mode: focused."""
        section = self._section()
        # The guidance should connect focused to checkout context
        assert "focused" in section, "AppSchemaAgent must reference focused shell_mode"
        # checkout and focused must both appear in the external-redirect section
        redirect_section = section[section.find("External-redirect"):]
        assert "focused" in redirect_section, (
            "External-redirect flow guidance must specify focused shell_mode for checkout initiation"
        )

    def test_landing_mentioned_for_static_cancellation_page(self):
        """AppSchemaAgent guidance must use landing page type for static return/cancellation pages."""
        section = self._section()
        redirect_section = section[section.find("External-redirect"):]
        assert "landing" in redirect_section, (
            "External-redirect guidance must specify landing page type for cancellation/return pages"
        )

    def test_record_list_mentioned_for_order_history(self):
        """AppSchemaAgent guidance must use record_list for order/payment history pages."""
        section = self._section()
        redirect_section = section[section.find("External-redirect"):]
        assert "record_list" in redirect_section, (
            "External-redirect guidance must specify record_list for order/payment history"
        )

    def test_workspace_shell_mode_for_order_history(self):
        """Order history page must use shell_mode: workspace."""
        section = self._section()
        redirect_section = section[section.find("External-redirect"):]
        assert "workspace" in redirect_section, (
            "External-redirect guidance must specify workspace shell_mode for order history"
        )

    def test_custom_route_bundle_required_for_post_redirect_confirmation(self):
        """Guidance must state custom_route_bundle is required for post-redirect confirmation."""
        section = self._section()
        redirect_section = section[section.find("External-redirect"):]
        assert "custom_route_bundle" in redirect_section, (
            "External-redirect guidance must specify custom_route_bundle for post-redirect pages"
        )
        # Must mention the three triggering conditions
        assert "query param" in redirect_section.lower() or "url query" in redirect_section.lower(), (
            "Guidance must mention URL query params as a trigger for custom_route_bundle"
        )
        assert "polling" in redirect_section.lower(), (
            "Guidance must mention polling as a trigger for custom_route_bundle"
        )
        assert "conditional" in redirect_section.lower(), (
            "Guidance must mention conditional rendering as a trigger for custom_route_bundle"
        )

    def test_redirect_alone_is_not_confirmation(self):
        """Guidance must warn against confirming payment/state from redirect URL alone."""
        section = self._section()
        redirect_section = section[section.find("External-redirect"):]
        # The word "alone" or equivalent must appear near confirmation language
        assert "redirect URL alone" in redirect_section or "redirect alone" in redirect_section.lower(), (
            "AppSchemaAgent guidance must warn that redirect URL alone is not state confirmation"
        )

    def test_api_endpoint_has_no_query_string_support(self):
        """Guidance must state api_endpoint does not support query strings."""
        section = self._section()
        redirect_section = section[section.find("External-redirect"):]
        assert "query string" in redirect_section.lower() or "no query" in redirect_section.lower(), (
            "Guidance must clarify api_endpoint has no query string support, "
            "explaining why post-redirect pages need custom_route_bundle"
        )


# ---------------------------------------------------------------------------
# 6-8: file_contracts.yaml page_bundle hard_constraints
# ---------------------------------------------------------------------------


class TestFileContractsCheckoutConstraints:
    """file_contracts page_bundle must encode the checkout escape-hatch constraints."""

    def _section(self) -> str:
        return _file_contracts_text()

    def test_custom_route_bundle_required_for_query_param_polling(self):
        """page_bundle hard_constraints must state custom_route_bundle for query-param polling."""
        text = self._section()
        assert "query params" in text or "URL query" in text or "query param" in text, (
            "file_contracts must mention query params in page_bundle constraints"
        )
        assert "custom_route_bundle" in text, (
            "file_contracts page_bundle must reference custom_route_bundle"
        )
        assert "polling" in text, (
            "file_contracts page_bundle must mention polling as a custom_route_bundle trigger"
        )

    def test_post_redirect_must_not_confirm_from_redirect_alone(self):
        """page_bundle must state post-redirect page cannot confirm from redirect URL alone."""
        text = self._section()
        assert "redirect URL alone" in text or "redirect alone" in text.lower(), (
            "file_contracts must warn that post-redirect pages cannot confirm from redirect URL alone"
        )

    def test_api_endpoint_is_post_only_no_query_strings(self):
        """page_bundle must state api_endpoint has no query string support."""
        text = self._section()
        assert "no query strings" in text or "POST path only" in text or "POST-only" in text or "POST path" in text, (
            "file_contracts must state api_endpoint is POST-only with no query string support"
        )


# ---------------------------------------------------------------------------
# 9-11: Neutral declarative fixture validation
# ---------------------------------------------------------------------------


class TestDeclarativeCheckoutPageFixtures:
    """
    Structural fixture tests for the three declarative checkout pages.
    Uses neutral names: checkout_module, ecommerce_checkout.
    No proprietary product names.
    """

    def _make_checkout_page(self) -> Dict[str, Any]:
        """Checkout initiation page: wizard + focused + Form."""
        return {
            "name": "Checkout",
            "route": "/checkout",
            "title": "Checkout",
            "page_type": "wizard",
            "layout": "full-width",
            "shell_mode": "focused",
            "navigation": None,
            "extensions": None,
            "sections": [
                {
                    "id": "checkout-form",
                    "primitive": "Form",
                    "title": "Complete Your Order",
                    "config": {
                        "fields": [
                            {"name": "item_id", "label": "Item", "type": "text", "required": True}
                        ],
                        "submit_label": "Proceed to Payment",
                        "submit_action": {
                            "action_type": "submit",
                            "api_endpoint": "/api/modules/checkout_module/initiate_checkout",
                        },
                    },
                    "event_triggers": [],
                    "roles": None,
                }
            ],
        }

    def _make_cancelled_page(self) -> Dict[str, Any]:
        """Cancellation/return page: landing + focused + no api_endpoint."""
        return {
            "name": "CheckoutCancelled",
            "route": "/checkout/cancelled",
            "title": "Payment Cancelled",
            "page_type": "landing",
            "layout": "full-width",
            "shell_mode": "focused",
            "navigation": None,
            "extensions": None,
            "sections": [
                {
                    "id": "cancelled-panel",
                    "primitive": "Panel",
                    "title": "Payment Cancelled",
                    "config": {
                        "title": "Your payment was cancelled",
                        "description": "You can try again from the checkout page.",
                        "actions": [
                            {"label": "Back to Checkout", "action_type": "navigate", "href": "/checkout"}
                        ],
                    },
                    "event_triggers": [],
                    "roles": None,
                }
            ],
        }

    def _make_order_history_page(self) -> Dict[str, Any]:
        """Order history page: record_list + workspace + DataTable."""
        return {
            "name": "OrderHistory",
            "route": "/orders",
            "title": "Order History",
            "page_type": "record_list",
            "layout": "grid",
            "shell_mode": "workspace",
            "navigation": {"id": "orders", "label": "Orders", "scope": "local", "order": 30, "visible": True},
            "extensions": None,
            "sections": [
                {
                    "id": "orders-table",
                    "primitive": "DataTable",
                    "title": "Orders",
                    "config": {
                        "api_endpoint": "/api/modules/checkout_module/list_orders",
                        "columns": [
                            {"key": "order_id", "label": "Order"},
                            {"key": "amount", "label": "Amount"},
                            {"key": "currency", "label": "Currency"},
                            {"key": "status", "label": "Status", "type": "status"},
                            {"key": "created_at", "label": "Date"},
                        ],
                        "selection": "none",
                        "search": True,
                        "empty": {
                            "title": "No orders yet",
                            "message": "Orders will appear here after checkout.",
                        },
                    },
                    "event_triggers": [],
                    "roles": None,
                }
            ],
        }

    def test_checkout_page_is_wizard(self):
        page = self._make_checkout_page()
        assert page["page_type"] == "wizard"

    def test_checkout_page_shell_mode_is_focused(self):
        page = self._make_checkout_page()
        assert page["shell_mode"] == "focused"

    def test_checkout_page_layout_is_full_width(self):
        page = self._make_checkout_page()
        assert page["layout"] == "full-width"

    def test_checkout_page_has_form_section(self):
        page = self._make_checkout_page()
        primitives = [s["primitive"] for s in page["sections"]]
        assert "Form" in primitives

    def test_checkout_page_form_has_api_endpoint(self):
        page = self._make_checkout_page()
        form_section = next(s for s in page["sections"] if s["primitive"] == "Form")
        endpoint = form_section["config"].get("submit_action", {}).get("api_endpoint", "")
        assert "/api/modules/" in endpoint, "Form submit_action must bind to app-owned module"

    def test_checkout_page_api_endpoint_has_no_query_string(self):
        page = self._make_checkout_page()
        form_section = next(s for s in page["sections"] if s["primitive"] == "Form")
        endpoint = form_section["config"].get("submit_action", {}).get("api_endpoint", "")
        assert "?" not in endpoint, "api_endpoint must not contain query strings"
        assert "#" not in endpoint, "api_endpoint must not contain fragments"

    def test_cancelled_page_is_landing(self):
        page = self._make_cancelled_page()
        assert page["page_type"] == "landing"

    def test_cancelled_page_shell_mode_is_focused(self):
        page = self._make_cancelled_page()
        assert page["shell_mode"] == "focused"

    def test_cancelled_page_has_no_api_endpoint_binding(self):
        """Static cancellation page must have no api_endpoint — it is purely declarative content."""
        page = self._make_cancelled_page()
        for section in page["sections"]:
            config = section.get("config", {})
            assert "api_endpoint" not in config, (
                f"Section {section['id']!r} must not declare api_endpoint — "
                "cancellation page is static content only"
            )

    def test_order_history_page_is_record_list(self):
        page = self._make_order_history_page()
        assert page["page_type"] == "record_list"

    def test_order_history_page_shell_mode_is_workspace(self):
        page = self._make_order_history_page()
        assert page["shell_mode"] == "workspace"

    def test_order_history_has_data_table_with_status_column(self):
        page = self._make_order_history_page()
        table = next(s for s in page["sections"] if s["primitive"] == "DataTable")
        status_cols = [c for c in table["config"]["columns"] if c.get("type") == "status"]
        assert len(status_cols) >= 1, "Order history DataTable must have a status-type column"

    def test_order_history_has_empty_state(self):
        page = self._make_order_history_page()
        table = next(s for s in page["sections"] if s["primitive"] == "DataTable")
        assert "empty" in table["config"], "Order history DataTable must declare an empty state"
        assert table["config"]["empty"].get("title"), "Empty state must have a title"

    def test_order_history_api_endpoint_is_module_path(self):
        page = self._make_order_history_page()
        table = next(s for s in page["sections"] if s["primitive"] == "DataTable")
        endpoint = table["config"]["api_endpoint"]
        assert endpoint.startswith("/api/modules/"), (
            "order_history api_endpoint must use /api/modules/ path"
        )
        assert "?" not in endpoint, "api_endpoint must not include query strings"


# ---------------------------------------------------------------------------
# 12-13: custom_route_bundle fixture for post-redirect confirmation
# ---------------------------------------------------------------------------


class TestPostRedirectConfirmationCustomRoute:
    """
    Post-redirect confirmation page must use custom_route_bundle.
    Fixture demonstrates: route_manifest + page_files + component registration.
    Uses neutral name 'PaymentConfirmationPage'.
    """

    def _make_custom_route_bundle(self) -> Dict[str, Any]:
        """Minimal custom_route_bundle for a post-redirect confirmation page."""
        page_source = (
            "import { LoadingState, Panel, ErrorState } from '@mozaiks/chat-ui/ui';\n"
            "import { PageFrame } from '@mozaiks/chat-ui';\n"
            "import { useState, useEffect } from 'react';\n"
            "export default function PaymentConfirmationPage() {\n"
            "  const [status, setStatus] = useState('loading');\n"
            "  const [order, setOrder] = useState(null);\n"
            "  useEffect(() => {\n"
            "    const params = new URLSearchParams(window.location.search);\n"
            "    const sessionId = params.get('session_id');\n"
            "    if (!sessionId) { setStatus('error'); return; }\n"
            "    let attempts = 0;\n"
            "    const interval = setInterval(async () => {\n"
            "      attempts++;\n"
            "      if (attempts > 15) { clearInterval(interval); setStatus('timeout'); return; }\n"
            "      const res = await fetch('/api/modules/checkout_module/get_order_status', {\n"
            "        method: 'POST',\n"
            "        headers: { 'Content-Type': 'application/json' },\n"
            "        body: JSON.stringify({ session_id: sessionId }),\n"
            "      });\n"
            "      if (!res.ok) return;\n"
            "      const data = await res.json();\n"
            "      if (data.status === 'paid') {\n"
            "        clearInterval(interval);\n"
            "        setOrder(data);\n"
            "        setStatus('confirmed');\n"
            "      }\n"
            "    }, 2000);\n"
            "    return () => clearInterval(interval);\n"
            "  }, []);\n"
            "  return (\n"
            "    <PageFrame name='payment-confirmation' layout='full-width'>\n"
            "      {status === 'loading' && <LoadingState message='Confirming your payment...' />}\n"
            "      {status === 'confirmed' && order && (\n"
            "        <Panel title='Payment Confirmed'>\n"
            "          <p>{order.receipt_id}</p>\n"
            "          <p>{order.amount} {order.currency}</p>\n"
            "          <p>{order.paid_at}</p>\n"
            "        </Panel>\n"
            "      )}\n"
            "      {(status === 'timeout' || status === 'error') && (\n"
            "        <ErrorState message='Payment confirmation timed out.' action={{ label: 'View Orders', href: '/orders' }} />\n"
            "      )}\n"
            "    </PageFrame>\n"
            "  );\n"
            "}\n"
        )
        return {
            "route_manifest": [
                {
                    "id": "payment-confirmation",
                    "path": "/checkout/success",
                    "component": "PaymentConfirmationPage",
                    "meta": {"requiresAuth": True},
                }
            ],
            "page_files": [
                {
                    "filename": "ui/pages/custom/PaymentConfirmationPage.jsx",
                    "registry_key": "PaymentConfirmationPage",
                    "content": page_source,
                }
            ],
        }

    def test_post_redirect_uses_custom_route_bundle(self):
        bundle = self._make_custom_route_bundle()
        assert bundle.get("route_manifest") is not None
        assert bundle.get("page_files") is not None

    def test_route_manifest_has_confirmation_route(self):
        bundle = self._make_custom_route_bundle()
        paths = [r["path"] for r in bundle["route_manifest"]]
        assert "/checkout/success" in paths, (
            "Post-redirect confirmation route must declare /checkout/success in route_manifest"
        )

    def test_page_files_registry_key_matches_component(self):
        bundle = self._make_custom_route_bundle()
        for route_entry in bundle["route_manifest"]:
            component = route_entry["component"]
            file_entry = next(
                (f for f in bundle["page_files"] if f["registry_key"] == component),
                None,
            )
            assert file_entry is not None, (
                f"route_manifest component {component!r} must have a matching page_files entry "
                f"with registry_key={component!r}"
            )

    def test_page_file_is_jsx(self):
        bundle = self._make_custom_route_bundle()
        for page_file in bundle["page_files"]:
            assert page_file["filename"].endswith(".jsx"), (
                f"Custom route page file {page_file['filename']!r} must use .jsx extension"
            )
            assert "ui/pages/custom/" in page_file["filename"], (
                f"Custom route page file must live under ui/pages/custom/"
            )

    def test_page_reads_session_id_from_url_params(self):
        """Custom route must extract session token from URL query params, not from static state."""
        bundle = self._make_custom_route_bundle()
        content = bundle["page_files"][0]["content"]
        # Must use URLSearchParams or equivalent to read query params
        assert "URLSearchParams" in content or "searchParams" in content.lower(), (
            "Post-redirect confirmation page must read session token from URL query params"
        )

    def test_page_polls_module_action(self):
        """Custom route must poll an app-owned module action, not a direct hosted endpoint."""
        bundle = self._make_custom_route_bundle()
        content = bundle["page_files"][0]["content"]
        assert "/api/modules/" in content, (
            "Post-redirect confirmation page must poll an app-owned module action"
        )
        assert "setInterval" in content or "poll" in content.lower(), (
            "Post-redirect confirmation page must implement polling"
        )

    def test_page_does_not_confirm_from_redirect_alone(self):
        """Confirmation must not trigger immediately on mount without polling the module."""
        bundle = self._make_custom_route_bundle()
        content = bundle["page_files"][0]["content"]
        # The confirmed state must be set inside the polling callback, not synchronously on load
        # Verify: status starts as 'loading', not 'confirmed'
        assert "useState('loading')" in content or "useState(\"loading\")" in content, (
            "Post-redirect confirmation page must initialize in loading state, "
            "not in confirmed state — confirmation comes from polling, not from redirect alone"
        )

    def test_page_uses_loading_and_error_states(self):
        bundle = self._make_custom_route_bundle()
        content = bundle["page_files"][0]["content"]
        assert "LoadingState" in content, "Post-redirect page must use LoadingState while polling"
        assert "ErrorState" in content, "Post-redirect page must use ErrorState on timeout"

    def test_page_uses_page_frame(self):
        bundle = self._make_custom_route_bundle()
        content = bundle["page_files"][0]["content"]
        assert "PageFrame" in content, (
            "Custom route page in ui/pages/custom/ must use PageFrame from @mozaiks/chat-ui"
        )

    def test_page_imports_from_chat_ui(self):
        bundle = self._make_custom_route_bundle()
        content = bundle["page_files"][0]["content"]
        assert "@mozaiks/chat-ui" in content, (
            "Custom route page must import shared primitives from @mozaiks/chat-ui"
        )

    def test_page_exposes_only_safe_fields(self):
        """Post-redirect confirmation must not surface provider IDs or private fields."""
        bundle = self._make_custom_route_bundle()
        content = bundle["page_files"][0]["content"]
        _forbidden = [
            "payment_intent_id",
            "session_id_raw",
            "customer_email",
            "provider_error",
        ]
        for field in _forbidden:
            assert field not in content, (
                f"Post-redirect confirmation page must not reference forbidden field {field!r}"
            )

    def test_page_has_orders_fallback_link(self):
        bundle = self._make_custom_route_bundle()
        content = bundle["page_files"][0]["content"]
        assert "/orders" in content, (
            "Post-redirect confirmation page error/timeout state must link to order history (/orders)"
        )


# ---------------------------------------------------------------------------
# 14: Declarative pages must not bind to external provider endpoints
# ---------------------------------------------------------------------------


class TestNoDirectProviderEndpointBinding:
    """Declarative pages must bind only to app-owned module endpoints."""

    def _validate_no_hosted_endpoint(self, page: Dict[str, Any]) -> List[str]:
        """
        Return a list of violations where a section api_endpoint bypasses an
        app-owned module and goes directly to an external payment provider path.

        For OSS purposes, the provider is identified by pattern:
        - endpoint path segment contains 'external_payment_provider' or 'hosted_pack'
        """
        violations = []
        for section in page.get("sections", []):
            config = section.get("config", {})
            endpoint = config.get("api_endpoint", "")
            if not endpoint:
                continue
            parts = endpoint.split("/")
            # /api/modules/{module_id}/{action_id} — module_id must be app-owned
            if len(parts) >= 4 and parts[1] == "api" and parts[2] == "modules":
                module_id = parts[3]
                # External provider IDs should not appear directly in page endpoints
                if module_id.startswith("external_payment_provider"):
                    violations.append(
                        f"Section {section['id']!r} binds directly to external provider "
                        f"endpoint {endpoint!r} — page must bind to app-owned module only"
                    )
        return violations

    def test_checkout_page_does_not_bind_to_external_provider(self):
        page = {
            "name": "Checkout",
            "route": "/checkout",
            "page_type": "wizard",
            "sections": [
                {
                    "id": "checkout-form",
                    "primitive": "Form",
                    "config": {
                        "submit_action": {
                            "api_endpoint": "/api/modules/ecommerce_checkout/initiate_checkout"
                        }
                    },
                }
            ],
        }
        violations = self._validate_no_hosted_endpoint(page)
        assert violations == [], f"Checkout page must not bind to provider endpoint: {violations}"

    def test_order_history_does_not_bind_to_external_provider(self):
        page = {
            "name": "OrderHistory",
            "route": "/orders",
            "page_type": "record_list",
            "sections": [
                {
                    "id": "orders-table",
                    "primitive": "DataTable",
                    "config": {
                        "api_endpoint": "/api/modules/ecommerce_checkout/list_orders"
                    },
                }
            ],
        }
        violations = self._validate_no_hosted_endpoint(page)
        assert violations == [], f"Order history must not bind to provider endpoint: {violations}"

    def test_direct_external_provider_endpoint_detected(self):
        """Validator correctly identifies direct external-provider endpoint binding."""
        page = {
            "name": "BadCheckout",
            "route": "/checkout",
            "page_type": "wizard",
            "sections": [
                {
                    "id": "bad-form",
                    "primitive": "Form",
                    "config": {
                        "api_endpoint": "/api/modules/external_payment_provider/charge"
                    },
                }
            ],
        }
        violations = self._validate_no_hosted_endpoint(page)
        assert len(violations) == 1, (
            "Validator must flag direct external_payment_provider endpoint binding"
        )
        assert "external_payment_provider" in violations[0]


# ---------------------------------------------------------------------------
# 15: No proprietary names in OSS guidance
# ---------------------------------------------------------------------------


class TestOSSProprietaryNamesAbsent:
    """OSS guidance and this test file must not reference proprietary product names."""

    def test_agents_yaml_checkout_section_has_no_proprietary_names(self):
        section = _app_schema_agent_section(_agents_yaml_text())
        redirect_section = section[section.find("External-redirect"):]
        for name in _PROPRIETARY_NAMES:
            assert name not in redirect_section, (
                f"Proprietary name {name!r} found in AppSchemaAgent external-redirect guidance. "
                "OSS guidance must use neutral ecommerce/checkout terms only."
            )

    def test_file_contracts_checkout_constraints_have_no_proprietary_names(self):
        text = _file_contracts_text()
        # Check the newly added constraints only (the post-redirect section)
        checkout_section = text[text.find("query params"):]
        for name in _PROPRIETARY_NAMES:
            assert name not in checkout_section, (
                f"Proprietary name {name!r} found in file_contracts checkout constraints. "
                "Constraints must be host-agnostic."
            )



