"""
Regression tests for platform notification infrastructure.

Covers:
1.  _render_template supports {{field}}, {{field | upper}}, {% if field %}...{% endif %}
2.  _render_template leaves unknown placeholders intact
3.  _create_notification falls back to flat envelope for payload + tenant extraction
4.  GET /api/notifications safe-projection excludes source_event and other internal fields
5.  GET /api/notifications endpoint declared in platform.py
6.  POST /api/notifications/{id}/read endpoint declared
7.  POST /api/notifications/mark-all-read endpoint declared
8.  _NOTIFICATION_SAFE_PROJECTION strips source_event
9. platform.py notification listing endpoint strips source_event (projection check)
"""
from __future__ import annotations

from pathlib import Path

_MOZAIKS_ROOT = Path(__file__).resolve().parents[1]
_MODULE_EVENT_ROUTER = _MOZAIKS_ROOT / "mozaiksai" / "core" / "runtime" / "composition" / "module_event_router.py"
_PLATFORM_PY = _MOZAIKS_ROOT / "mozaiksai" / "hosts" / "platform.py"
# Notification routes were extracted to this router module.
_NOTIFICATIONS_ROUTER_PY = _MOZAIKS_ROOT / "mozaiksai" / "hosts" / "routers" / "notifications.py"


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

    def test_payload_dot_syntax_is_not_rendered(self):
        result = self._render("{payload.amount} {payload.currency}", {"amount": 500, "currency": "gbp"})
        assert result == "{payload.amount} {payload.currency}"

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
    """GET /api/notifications is declared in the notifications router and projects source_event out."""

    def _router_source(self) -> str:
        return _NOTIFICATIONS_ROUTER_PY.read_text(encoding="utf-8")

    def _platform_source(self) -> str:
        return _PLATFORM_PY.read_text(encoding="utf-8")

    def test_get_notifications_endpoint_declared(self):
        source = self._router_source()
        assert '@router.get("/api/notifications")' in source, (
            "GET /api/notifications must be declared in the notifications router"
        )

    def test_platform_includes_notifications_router(self):
        source = self._platform_source()
        assert "_notifications_router" in source or "notifications" in source, (
            "platform.py must include the notifications router"
        )

    def test_notification_safe_projection_strips_source_event(self):
        source = self._router_source()
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
        source = self._router_source()
        # The listing endpoint must reference the safe projection constant
        listing_start = source.index('@router.get("/api/notifications")')
        listing_end = source.find("@router.", listing_start + 10)
        if listing_end == -1:
            listing_end = len(source)
        listing_block = source[listing_start:listing_end]
        assert "_NOTIFICATION_SAFE_PROJECTION" in listing_block, (
            "GET /api/notifications must apply _NOTIFICATION_SAFE_PROJECTION"
        )

    def test_listing_does_not_return_source_event_field(self):
        source = self._router_source()
        listing_start = source.index('@router.get("/api/notifications")')
        listing_end = source.find("@router.", listing_start + 10)
        if listing_end == -1:
            listing_end = len(source)
        listing_block = source[listing_start:listing_end]
        # source_event must not appear in the return body (only in projection)
        assert '"source_event"' not in listing_block.split("_NOTIFICATION_SAFE_PROJECTION")[1], (
            "GET /api/notifications must not reference source_event in return body"
        )

    def test_listing_supports_status_filter(self):
        source = self._router_source()
        listing_start = source.index('@router.get("/api/notifications")')
        listing_end = source.find("@router.", listing_start + 10)
        if listing_end == -1:
            listing_end = len(source)
        listing_block = source[listing_start:listing_end]
        assert "status" in listing_block, (
            "GET /api/notifications must support status filter parameter"
        )

    def test_listing_sorts_by_created_at_desc(self):
        source = self._router_source()
        listing_start = source.index('@router.get("/api/notifications")')
        listing_end = source.find("@router.", listing_start + 10)
        if listing_end == -1:
            listing_end = len(source)
        listing_block = source[listing_start:listing_end]
        assert "created_at" in listing_block and "-1" in listing_block, (
            "GET /api/notifications must sort by created_at descending"
        )

    def test_listing_requires_auth(self):
        source = self._router_source()
        listing_start = source.index('@router.get("/api/notifications")')
        listing_end = source.find("@router.", listing_start + 10)
        if listing_end == -1:
            listing_end = len(source)
        listing_block = source[listing_start:listing_end]
        assert "require_user_scope" in listing_block or "Depends" in listing_block, (
            "GET /api/notifications must require authentication"
        )


# ---------------------------------------------------------------------------
# Mark-read endpoints
# ---------------------------------------------------------------------------

class TestMarkReadEndpoints:
    def _router_source(self) -> str:
        return _NOTIFICATIONS_ROUTER_PY.read_text(encoding="utf-8")

    def test_mark_single_read_endpoint_declared(self):
        source = self._router_source()
        assert "/api/notifications/{notification_id}/read" in source, (
            "POST /api/notifications/{notification_id}/read must be declared"
        )

    def test_mark_all_read_endpoint_declared(self):
        source = self._router_source()
        assert "/api/notifications/mark-all-read" in source, (
            "POST /api/notifications/mark-all-read must be declared"
        )

    def test_mark_single_read_requires_auth(self):
        source = self._router_source()
        idx = source.index("/api/notifications/{notification_id}/read")
        block = source[max(0, idx - 100): idx + 400]
        assert "require_user_scope" in block or "Depends" in block


# ---------------------------------------------------------------------------
# Notification visibility filter helper
# ---------------------------------------------------------------------------

class TestVisibilityFilterHelper:
    def test_notification_visibility_filter_declared(self):
        source = _NOTIFICATIONS_ROUTER_PY.read_text(encoding="utf-8")
        assert "_notification_visibility_filter" in source, (
            "_notification_visibility_filter helper must be declared in the notifications router"
        )

    def test_visibility_filter_checks_roles(self):
        source = _NOTIFICATIONS_ROUTER_PY.read_text(encoding="utf-8")
        idx = source.index("_notification_visibility_filter")
        block = source[idx: idx + 600]
        assert "audience.roles" in block, (
            "_notification_visibility_filter must filter by audience.roles"
        )


# ---------------------------------------------------------------------------
# context_fields — _is_secret_context_key
# ---------------------------------------------------------------------------

class TestIsSecretContextKey:
    """_is_secret_context_key must recognise known secret-shaped key patterns."""

    def _check(self, key: str) -> bool:
        from mozaiksai.core.runtime.composition.module_event_router import _is_secret_context_key
        return _is_secret_context_key(key)

    def test_stripe_prefix_is_secret(self):
        assert self._check("stripe_payment_intent_id") is True

    def test_idempotency_prefix_is_secret(self):
        assert self._check("idempotency_key") is True

    def test_token_suffix_is_secret(self):
        assert self._check("access_token") is True

    def test_key_suffix_is_secret(self):
        assert self._check("api_key") is True

    def test_secret_suffix_is_secret(self):
        assert self._check("client_secret") is True

    def test_password_is_secret(self):
        assert self._check("password") is True

    def test_credential_is_secret(self):
        assert self._check("credential_id") is True

    def test_task_id_not_secret(self):
        assert self._check("task_id") is False

    def test_approval_id_not_secret(self):
        assert self._check("approval_id") is False

    def test_record_id_not_secret(self):
        assert self._check("record_id") is False

    def test_app_id_not_secret(self):
        assert self._check("app_id") is False

    def test_amount_not_secret(self):
        assert self._check("amount") is False


# ---------------------------------------------------------------------------
# context_fields — behaviour in _create_notification
# ---------------------------------------------------------------------------

class TestContextFieldsBehaviour:
    """
    Notification rules with context_fields produce notification.context.
    Rules without context_fields produce no context key.
    Secret-shaped keys are stripped defensively.

    Neutral examples: task_id, approval_id, record_id — no proprietary names.
    """

    def _make_router_and_store(self, rule: dict):
        import asyncio

        from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter

        stored: list[dict] = []

        async def store(record: dict) -> None:
            stored.append(record)

        # Minimal LoadedModule stub
        class _Manifests:
            def __init__(self, notifs):
                class _R:
                    reactions = []
                self.reactions = _R()
                class _N:
                    def __init__(self, n):
                        self.notifications = n
                self.notifications = _N(notifs)

        class _Mod:
            def __init__(self, name, notifs):
                self.name = name
                self.handler = None
                self.manifests = _Manifests(notifs)

        router = ModuleEventRouter([_Mod("test_module", [rule])], notification_store=store)
        return router, stored, asyncio

    def _envelope(self, payload: dict) -> dict:
        return {
            "id": "evt_001",
            "type": "test.event",
            "version": 1,
            "occurred_at": "2026-01-01T00:00:00+00:00",
            "tenant": {"app_id": "app_test", "tenant_id": "ten_test"},
            "payload": payload,
            "visibility": "internal",
        }

    def test_declared_fields_appear_in_context(self):
        rule = {
            "id": "task.created.notify",
            "event_type": "task.created",
            "context_fields": ["task_id", "record_id"],
            "template": {"title": "New task", "body": ""},
        }
        router, stored, asyncio = self._make_router_and_store(rule)
        asyncio.run(
            router.handle_event("task.created", self._envelope(
                {"task_id": "tsk_abc", "record_id": "rec_123", "extra": "ignore"}
            ))
        )
        ctx = stored[0].get("context")
        assert ctx is not None
        assert ctx["task_id"] == "tsk_abc"
        assert ctx["record_id"] == "rec_123"

    def test_undeclared_fields_absent_from_context(self):
        rule = {
            "id": "task.created.notify",
            "event_type": "task.created",
            "context_fields": ["task_id"],
            "template": {"title": "New task", "body": ""},
        }
        router, stored, asyncio = self._make_router_and_store(rule)
        asyncio.run(
            router.handle_event("task.created", self._envelope(
                {"task_id": "tsk_abc", "user_name": "alice", "internal_ref": "x"}
            ))
        )
        ctx = stored[0].get("context", {})
        assert "user_name" not in ctx
        assert "internal_ref" not in ctx

    def test_secret_keys_stripped_from_context(self):
        rule = {
            "id": "approval.created.notify",
            "event_type": "approval.created",
            "context_fields": ["approval_id", "stripe_payment_intent_id", "api_key"],
            "template": {"title": "Approval", "body": ""},
        }
        router, stored, asyncio = self._make_router_and_store(rule)
        asyncio.run(
            router.handle_event("approval.created", self._envelope({
                "approval_id": "appr_xyz",
                "stripe_payment_intent_id": "pi_secret_123",
                "api_key": "sk_live_abc",
            }))
        )
        ctx = stored[0].get("context", {})
        assert ctx.get("approval_id") == "appr_xyz"
        assert "stripe_payment_intent_id" not in ctx
        assert "api_key" not in ctx

    def test_no_context_fields_means_no_context_key(self):
        rule = {
            "id": "record.updated.notify",
            "event_type": "record.updated",
            "template": {"title": "Updated", "body": ""},
        }
        router, stored, asyncio = self._make_router_and_store(rule)
        asyncio.run(
            router.handle_event("record.updated", self._envelope({"record_id": "rec_001"}))
        )
        assert "context" not in stored[0] or stored[0].get("context") is None

    def test_empty_context_fields_list_means_no_context_key(self):
        rule = {
            "id": "record.updated.notify",
            "event_type": "record.updated",
            "context_fields": [],
            "template": {"title": "Updated", "body": ""},
        }
        router, stored, asyncio = self._make_router_and_store(rule)
        asyncio.run(
            router.handle_event("record.updated", self._envelope({"record_id": "rec_001"}))
        )
        assert not stored[0].get("context")

    def test_all_secret_fields_produces_no_context(self):
        rule = {
            "id": "sensitive.notify",
            "event_type": "sensitive.event",
            "context_fields": ["stripe_id", "api_key"],
            "template": {"title": "Sensitive", "body": ""},
        }
        router, stored, asyncio = self._make_router_and_store(rule)
        asyncio.run(
            router.handle_event("sensitive.event", self._envelope({"stripe_id": "x", "api_key": "y"}))
        )
        assert not stored[0].get("context")

    def test_source_event_still_stored_internally(self):
        rule = {
            "id": "task.created.notify",
            "event_type": "task.created",
            "context_fields": ["task_id"],
            "template": {"title": "Task created", "body": ""},
        }
        router, stored, asyncio = self._make_router_and_store(rule)
        asyncio.run(
            router.handle_event("task.created", self._envelope({"task_id": "tsk_abc"}))
        )
        assert stored[0].get("source_event") is not None


# ---------------------------------------------------------------------------
# context_fields — platform projection must NOT strip context
# ---------------------------------------------------------------------------

class TestContextFieldsPlatformProjection:
    """
    _NOTIFICATION_SAFE_PROJECTION is an exclusion projection.
    The 'context' field must NOT be in the exclusion list so it passes through.
    """

    def test_context_not_in_safe_projection(self):
        source = _NOTIFICATIONS_ROUTER_PY.read_text(encoding="utf-8")
        proj_start = source.index("_NOTIFICATION_SAFE_PROJECTION")
        # Find the closing brace of the dict literal
        proj_block = source[proj_start: proj_start + 600]
        assert '"context": 0' not in proj_block, (
            "context must NOT be excluded by _NOTIFICATION_SAFE_PROJECTION — "
            "it must pass through to callers when present"
        )
        assert "'context': 0" not in proj_block, (
            "context must NOT be excluded by _NOTIFICATION_SAFE_PROJECTION"
        )


# ---------------------------------------------------------------------------
# context_fields — flat envelope compatibility
# ---------------------------------------------------------------------------

class TestContextFieldsFlatEnvelope:
    """context_fields must resolve from flattened scalar payloads (non-nested events)."""

    def test_context_resolved_from_flat_envelope(self):
        import asyncio

        from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter

        stored: list[dict] = []

        async def store(record: dict) -> None:
            stored.append(record)

        class _Manifests:
            def __init__(self, notifs):
                class _R:
                    reactions = []
                self.reactions = _R()
                class _N:
                    def __init__(self, n):
                        self.notifications = n
                self.notifications = _N(notifs)

        class _Mod:
            def __init__(self, name, notifs):
                self.name = name
                self.handler = None
                self.manifests = _Manifests(notifs)

        rule = {
            "id": "order.placed.notify",
            "event_type": "order.placed",
            "context_fields": ["record_id", "app_id"],
            "template": {"title": "Order placed", "body": ""},
        }
        router = ModuleEventRouter([_Mod("order_module", [rule])], notification_store=store)

        flat_envelope = {
            "id": "evt_flat_001",
            "type": "order.placed",
            "version": 1,
            "occurred_at": "2026-01-01T00:00:00+00:00",
            "record_id": "ord_xyz",
            "app_id": "app_store",
            "amount": 99,
        }
        asyncio.run(
            router.handle_event("order.placed", flat_envelope)
        )

        assert len(stored) == 1
        ctx = stored[0].get("context")
        assert ctx is not None
        assert ctx["record_id"] == "ord_xyz"
        assert ctx["app_id"] == "app_store"

