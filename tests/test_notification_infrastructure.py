"""
Regression tests for platform notification infrastructure.

Covers:
1.  _render_template supports {{field}}, {{field | upper}}, {% if field %}...{% endif %}
2.  _render_template backward-compat: legacy {payload.field} still works
3.  _render_template leaves unknown placeholders intact
4.  _create_notification falls back to flat envelope for payload + tenant extraction
5.  GET /api/notifications safe-projection excludes source_event and other internal fields
6.  GET /api/notifications endpoint declared in platform.py
7.  POST /api/notifications/{id}/read endpoint declared
8.  POST /api/notifications/mark-all-read endpoint declared
9.  _NOTIFICATION_SAFE_PROJECTION strips source_event
10. platform.py notification listing endpoint strips source_event (projection check)
"""
from __future__ import annotations

from pathlib import Path

import pytest


_MOZAIKS_ROOT = Path(__file__).resolve().parents[1]
_MODULE_EVENT_ROUTER = _MOZAIKS_ROOT / "mozaiksai" / "core" / "runtime" / "composition" / "module_event_router.py"
_PLATFORM_PY = _MOZAIKS_ROOT / "mozaiksai" / "hosts" / "platform.py"


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

class TestRenderTemplate:
    """_render_template supports the template syntaxes used in notifications.yaml."""

    def _render(self, template: str, payload: dict) -> str:
        from mozaiksai.core.runtime.composition.module_event_router import _render_template
        return _render_template(template, payload)

    def test_plain_substitution(self):
        result = self._render("Hello {{name}}", {"name": "world"})
        assert result == "Hello world"

    def test_upper_filter(self):
        result = self._render("{{currency | upper}}", {"currency": "usd"})
        assert result == "USD"

    def test_plain_substitution_multiple_fields(self):
        result = self._render("{{amount}} {{currency | upper}}", {"amount": 2500, "currency": "usd"})
        assert result == "2500 USD"

    def test_conditional_truthy(self):
        result = self._render(
            "{% if failure_reason %}Reason: {{failure_reason}}.{% endif %}",
            {"failure_reason": "card_declined"},
        )
        assert result == "Reason: card_declined."

    def test_conditional_falsy_empty(self):
        result = self._render(
            "{% if failure_reason %}Reason: {{failure_reason}}.{% endif %}",
            {"failure_reason": ""},
        )
        assert result == ""

    def test_conditional_falsy_missing(self):
        result = self._render(
            "{% if failure_reason %}Reason: {{failure_reason}}.{% endif %}",
            {},
        )
        assert result == ""

    def test_payment_succeeded_template(self):
        template = "A payment of {{amount}} {{currency | upper}} was completed for app {{app_id}}."
        result = self._render(template, {"amount": 2500, "currency": "usd", "app_id": "my_app"})
        assert result == "A payment of 2500 USD was completed for app my_app."

    def test_payment_failed_template(self):
        template = (
            "A payment of {{amount}} {{currency | upper}} failed for app {{app_id}}. "
            "{% if failure_reason %}Reason: {{failure_reason}}.{% endif %}"
        )
        result = self._render(template, {
            "amount": 1000, "currency": "usd", "app_id": "shop_app", "failure_reason": "card_declined"
        })
        assert "1000 USD" in result
        assert "Reason: card_declined." in result

    def test_payment_failed_no_reason(self):
        template = (
            "A payment failed. "
            "{% if failure_reason %}Reason: {{failure_reason}}.{% endif %}"
        )
        result = self._render(template, {"failure_reason": ""})
        assert result == "A payment failed. "

    def test_legacy_payload_dot_syntax(self):
        result = self._render("{payload.amount} {payload.currency}", {"amount": 500, "currency": "gbp"})
        assert result == "500 gbp"

    def test_unknown_placeholder_left_intact(self):
        result = self._render("{{unknown}}", {})
        assert result == "{{unknown}}"

    def test_whitespace_in_braces(self):
        result = self._render("{{ amount }}", {"amount": 99})
        assert result == "99"


# ---------------------------------------------------------------------------
# Flat event envelope fallback in _create_notification
# ---------------------------------------------------------------------------

class TestFlatEnvelopeFallback:
    """
    module_event_router._create_notification must handle flat event dicts
    (emitted without tenant/payload nesting) by using the envelope scalars
    as both the template payload and the app_id/tenant_id source.
    """

    def test_source_contains_flat_envelope_handling(self):
        source = _MODULE_EVENT_ROUTER.read_text(encoding="utf-8")
        # The fix extracts app_id from envelope root when tenant is empty
        assert "envelope.get(\"app_id\")" in source or "envelope.get('app_id')" in source, (
            "_create_notification must fall back to envelope.app_id for flat event dicts"
        )

    def test_payload_fallback_comment_or_code_present(self):
        source = _MODULE_EVENT_ROUTER.read_text(encoding="utf-8")
        # The fix must not leave only `envelope.get("payload")` with no fallback
        assert "if not payload" in source or "raw_payload" in source, (
            "_create_notification must handle missing payload key (flat event dicts)"
        )

    def test_render_template_import_re(self):
        source = _MODULE_EVENT_ROUTER.read_text(encoding="utf-8")
        assert "import re" in source, (
            "module_event_router.py must import re for template rendering"
        )


# ---------------------------------------------------------------------------
# GET /api/notifications listing endpoint
# ---------------------------------------------------------------------------

class TestNotificationsListingEndpoint:
    """GET /api/notifications is declared in platform.py and projects source_event out."""

    def _platform_source(self) -> str:
        return _PLATFORM_PY.read_text(encoding="utf-8")

    def test_get_notifications_endpoint_declared(self):
        source = self._platform_source()
        assert '@app.get("/api/notifications")' in source, (
            "GET /api/notifications must be declared in platform.py"
        )

    def test_notification_safe_projection_strips_source_event(self):
        source = self._platform_source()
        assert "_NOTIFICATION_SAFE_PROJECTION" in source, (
            "_NOTIFICATION_SAFE_PROJECTION constant must be defined"
        )
        # Projection must exclude source_event
        proj_start = source.index("_NOTIFICATION_SAFE_PROJECTION")
        proj_block = source[proj_start: proj_start + 600]
        assert "source_event" in proj_block, (
            "_NOTIFICATION_SAFE_PROJECTION must include source_event: 0 to strip provider IDs"
        )
        assert '"source_event": 0' in proj_block or "'source_event': 0" in proj_block, (
            "source_event must be explicitly projected out (set to 0)"
        )

    def test_listing_applies_safe_projection(self):
        source = self._platform_source()
        # The listing endpoint must reference the safe projection constant
        listing_start = source.index('@app.get("/api/notifications")')
        # Find the closing function boundary (next @app. decorator)
        listing_end = source.find("@app.", listing_start + 10)
        listing_block = source[listing_start:listing_end]
        assert "_NOTIFICATION_SAFE_PROJECTION" in listing_block, (
            "GET /api/notifications must apply _NOTIFICATION_SAFE_PROJECTION"
        )

    def test_listing_does_not_return_source_event_field(self):
        source = self._platform_source()
        listing_start = source.index('@app.get("/api/notifications")')
        listing_end = source.find("@app.", listing_start + 10)
        listing_block = source[listing_start:listing_end]
        # source_event must not appear in the return body (only in projection)
        assert '"source_event"' not in listing_block.split("_NOTIFICATION_SAFE_PROJECTION")[1], (
            "GET /api/notifications must not reference source_event in return body"
        )

    def test_listing_supports_status_filter(self):
        source = self._platform_source()
        listing_start = source.index('@app.get("/api/notifications")')
        listing_end = source.find("@app.", listing_start + 10)
        listing_block = source[listing_start:listing_end]
        assert "status" in listing_block, (
            "GET /api/notifications must support status filter parameter"
        )

    def test_listing_sorts_by_created_at_desc(self):
        source = self._platform_source()
        listing_start = source.index('@app.get("/api/notifications")')
        listing_end = source.find("@app.", listing_start + 10)
        listing_block = source[listing_start:listing_end]
        assert "created_at" in listing_block and "-1" in listing_block, (
            "GET /api/notifications must sort by created_at descending"
        )

    def test_listing_requires_auth(self):
        source = self._platform_source()
        listing_start = source.index('@app.get("/api/notifications")')
        listing_end = source.find("@app.", listing_start + 10)
        listing_block = source[listing_start:listing_end]
        assert "require_user_scope" in listing_block or "Depends" in listing_block, (
            "GET /api/notifications must require authentication"
        )


# ---------------------------------------------------------------------------
# Mark-read endpoints
# ---------------------------------------------------------------------------

class TestMarkReadEndpoints:
    def _platform_source(self) -> str:
        return _PLATFORM_PY.read_text(encoding="utf-8")

    def test_mark_single_read_endpoint_declared(self):
        source = self._platform_source()
        assert "/api/notifications/{notification_id}/read" in source, (
            "POST /api/notifications/{notification_id}/read must be declared"
        )

    def test_mark_all_read_endpoint_declared(self):
        source = self._platform_source()
        assert "/api/notifications/mark-all-read" in source, (
            "POST /api/notifications/mark-all-read must be declared"
        )

    def test_mark_single_read_requires_auth(self):
        source = self._platform_source()
        idx = source.index("/api/notifications/{notification_id}/read")
        block = source[max(0, idx - 100): idx + 400]
        assert "require_user_scope" in block or "Depends" in block


# ---------------------------------------------------------------------------
# Notification visibility filter helper
# ---------------------------------------------------------------------------

class TestVisibilityFilterHelper:
    def test_notification_visibility_filter_declared(self):
        source = _PLATFORM_PY.read_text(encoding="utf-8")
        assert "_notification_visibility_filter" in source, (
            "_notification_visibility_filter helper must be declared in platform.py"
        )

    def test_visibility_filter_checks_roles(self):
        source = _PLATFORM_PY.read_text(encoding="utf-8")
        idx = source.index("_notification_visibility_filter")
        block = source[idx: idx + 600]
        assert "audience.roles" in block, (
            "_notification_visibility_filter must filter by audience.roles"
        )
