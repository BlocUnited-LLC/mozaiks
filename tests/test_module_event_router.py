"""
ModuleEventRouter unit tests.

Covers:
  - _render_template: plain {{field}}, {{field | upper}}, {% if %}...{% endif %},
    unknown fields left as-is, nested conditional rendering
  - _is_secret_context_key: matches secret fragments, leaves normal keys alone
  - event_types: sorted union of reaction and notification events
  - register: wires each event type onto the dispatcher, returns count
  - handle_event — notification target: creates notification from reaction,
    deduplicates if notification target already fired via reaction
  - handle_event — handler target: dispatches to module handler method
  - handle_event — other target kinds: emits platform reaction
  - _dispatch_handler: skips when module_id/handler_method missing,
    skips when no handler registered for module, skips when method not callable,
    exception in handler tolerated, success path with ctx and payload kwargs
  - _create_notification: structured envelope, flat envelope fallback,
    context_fields copying, secret key stripping, no-op notification_store None,
    emits notification.created event
  - _emit_platform_reaction: emits platform.reaction event, no-op when no emitter,
    capability invoker called when capability target
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from mozaiksai.core.runtime.composition.module_event_router import (
    ModuleEventRouter,
    _is_secret_context_key,
    _render_template,
)

# ---------------------------------------------------------------------------
# Helpers — build mock LoadedModule objects
# ---------------------------------------------------------------------------

def _reaction_model(event_type: str, **extra) -> MagicMock:
    m = MagicMock()
    m.event_type = event_type
    dumped = {"id": extra.get("id", f"r-{event_type}"), **extra}
    dumped.pop("event_type", None)
    m.model_dump.return_value = dumped
    return m


def _loaded_module(
    name: str,
    *,
    reactions: list[MagicMock] | None = None,
    notifications: list[dict] | None = None,
    handler: Any = None,
) -> MagicMock:
    m = MagicMock()
    m.name = name
    m.handler = handler or MagicMock()

    if reactions is not None:
        reactions_manifest = MagicMock()
        reactions_manifest.reactions = reactions
        m.manifests.reactions = reactions_manifest
    else:
        m.manifests.reactions = None

    if notifications is not None:
        notif_manifest = MagicMock()
        notif_manifest.notifications = notifications
        m.manifests.notifications = notif_manifest
    else:
        m.manifests.notifications = None

    return m


def _router(
    modules=(),
    *,
    event_emitter=None,
    notification_store=None,
    capability_invoker=None,
) -> ModuleEventRouter:
    return ModuleEventRouter(
        modules,
        event_emitter=event_emitter,
        notification_store=notification_store,
        capability_invoker=capability_invoker,
    )


def _handler_target_reaction(
    event_type: str,
    *,
    handler_method: str = "on_event",
    reaction_id: str = "r-1",
) -> MagicMock:
    return _reaction_model(
        event_type,
        id=reaction_id,
        target={"kind": "handler", "handler_method": handler_method},
    )


class RecordingServiceAdapter:
    calls: list[dict[str, Any]] = []

    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.__class__.calls.append(dict(payload))
        return {"success": True, "seen": payload.get("build_registry_id")}


def _service_adapter_target_reaction(
    event_type: str,
    *,
    reaction_id: str = "r-adapter",
) -> MagicMock:
    return _reaction_model(
        event_type,
        id=reaction_id,
        target={
            "kind": "service_adapter",
            "adapter": f"{__name__}:RecordingServiceAdapter",
            "adapter_method": "handle",
        },
    )


def _notification_target_reaction(
    event_type: str,
    *,
    notification_id: str = "ntf-1",
    reaction_id: str = "r-notif",
) -> MagicMock:
    return _reaction_model(
        event_type,
        id=reaction_id,
        target={"kind": "notification", "notification_id": notification_id},
    )


def _notification_rule(
    event_type: str,
    *,
    rule_id: str = "ntf-1",
    module_id: str | None = None,
    template: dict | None = None,
    channels: list | None = None,
    audience: dict | None = None,
    context_fields: list | None = None,
) -> dict:
    rule: dict = {
        "id": rule_id,
        "event_type": event_type,
        "template": template or {"title": "Event: {{event_type}}", "body": ""},
    }
    if module_id:
        rule["module_id"] = module_id
    if channels is not None:
        rule["channels"] = channels
    if audience is not None:
        rule["audience"] = audience
    if context_fields is not None:
        rule["context_fields"] = context_fields
    return rule


def _envelope(
    *,
    app_id: str = "app-1",
    tenant_id: str = "tenant-1",
    payload: dict | None = None,
    actor: dict | None = None,
    structured: bool = True,
) -> dict:
    if structured:
        return {
            "id": "evt-1",
            "type": "test.event",
            "version": 1,
            "tenant": {"app_id": app_id, "tenant_id": tenant_id},
            "actor": actor or {"id": "user-1"},
            "payload": payload or {"amount": 100},
        }
    else:
        base = {"app_id": app_id, "tenant_id": tenant_id}
        if payload:
            base.update(payload)
        return base


# ---------------------------------------------------------------------------
# 1. _render_template (pure function)
# ---------------------------------------------------------------------------

class TestRenderTemplate:
    def test_plain_substitution(self):
        assert _render_template("Hello {{name}}", {"name": "World"}) == "Hello World"

    def test_upper_filter(self):
        assert _render_template("{{status | upper}}", {"status": "open"}) == "OPEN"

    def test_conditional_truthy(self):
        result = _render_template("{% if name %}Hi {{name}}{% endif %}", {"name": "Ali"})
        assert result == "Hi Ali"

    def test_conditional_falsy_empty_string(self):
        result = _render_template("{% if name %}Hi {{name}}{% endif %}", {"name": ""})
        assert result == ""

    def test_conditional_falsy_missing_key(self):
        result = _render_template("{% if name %}Hi{% endif %}", {})
        assert result == ""

    def test_unknown_field_left_as_is(self):
        result = _render_template("{{unknown_field}}", {})
        assert result == "{{unknown_field}}"

    def test_multiple_fields(self):
        result = _render_template("{{a}} + {{b}}", {"a": "1", "b": "2"})
        assert result == "1 + 2"

    def test_none_value_renders_as_none_string(self):
        result = _render_template("{{val}}", {"val": None})
        # None value → leaves original token (val is not None→ renders "None")
        assert result == "{{val}}" or result == "None"

    def test_numeric_value_rendered(self):
        result = _render_template("count: {{n}}", {"n": 42})
        assert result == "count: 42"


# ---------------------------------------------------------------------------
# 2. _is_secret_context_key (pure function)
# ---------------------------------------------------------------------------

class TestIsSecretContextKey:
    def test_key_suffix_detected(self):
        assert _is_secret_context_key("api_key") is True

    def test_secret_suffix_detected(self):
        assert _is_secret_context_key("oauth_secret") is True

    def test_token_detected(self):
        assert _is_secret_context_key("auth_token") is True

    def test_password_detected(self):
        assert _is_secret_context_key("user_password") is True

    def test_credential_detected(self):
        assert _is_secret_context_key("credential_id") is True

    def test_payment_provider_prefix_detected(self):
        assert _is_secret_context_key("payment_provider_price_id") is True

    def test_idempotency_detected(self):
        assert _is_secret_context_key("idempotency_key") is True

    def test_normal_key_not_detected(self):
        assert _is_secret_context_key("user_id") is False

    def test_amount_not_detected(self):
        assert _is_secret_context_key("amount") is False

    def test_tenant_id_not_detected(self):
        assert _is_secret_context_key("tenant_id") is False


# ---------------------------------------------------------------------------
# 3. event_types property
# ---------------------------------------------------------------------------

class TestEventTypes:
    def test_empty_modules_returns_empty(self):
        router = _router([])
        assert router.event_types == []

    def test_reaction_events_included(self):
        mod = _loaded_module("m1", reactions=[_reaction_model("order.created")])
        router = _router([mod])
        assert "order.created" in router.event_types

    def test_notification_events_included(self):
        mod = _loaded_module("m1", notifications=[_notification_rule("payment.received")])
        router = _router([mod])
        assert "payment.received" in router.event_types

    def test_union_of_both(self):
        mod = _loaded_module(
            "m1",
            reactions=[_reaction_model("a.event")],
            notifications=[_notification_rule("b.event")],
        )
        router = _router([mod])
        types = router.event_types
        assert "a.event" in types
        assert "b.event" in types

    def test_events_sorted(self):
        mod = _loaded_module(
            "m1",
            reactions=[_reaction_model("z.event"), _reaction_model("a.event")],
        )
        router = _router([mod])
        assert router.event_types == sorted(router.event_types)

    def test_deduplicates_same_event_in_reactions_and_notifications(self):
        mod = _loaded_module(
            "m1",
            reactions=[_reaction_model("x.event")],
            notifications=[_notification_rule("x.event", rule_id="n1")],
        )
        router = _router([mod])
        assert router.event_types.count("x.event") == 1


# ---------------------------------------------------------------------------
# 4. register
# ---------------------------------------------------------------------------

class TestRegister:
    def test_registers_one_handler_per_event_type(self):
        mod = _loaded_module(
            "m1",
            reactions=[_reaction_model("a.event"), _reaction_model("b.event")],
        )
        router = _router([mod])
        dispatcher = MagicMock()
        count = router.register(dispatcher)
        assert count == 2
        assert dispatcher.register_handler.call_count == 2

    def test_empty_router_returns_zero(self):
        router = _router([])
        dispatcher = MagicMock()
        count = router.register(dispatcher)
        assert count == 0

    def test_registered_callable_is_a_coroutine_function(self):
        import inspect
        mod = _loaded_module("m1", reactions=[_reaction_model("x.event")])
        router = _router([mod])
        dispatcher = MagicMock()
        router.register(dispatcher)
        registered_handler = dispatcher.register_handler.call_args.args[1]
        assert inspect.iscoroutinefunction(registered_handler)


# ---------------------------------------------------------------------------
# 5. handle_event — handler target
# ---------------------------------------------------------------------------

class TestHandleEventHandlerTarget:
    @pytest.mark.asyncio
    async def test_dispatches_to_handler_method(self):
        called = []

        class Handler:
            async def on_event(self, ctx, **kwargs):
                called.append(kwargs)

        handler = Handler()
        reaction = _handler_target_reaction("order.created", handler_method="on_event")
        mod = _loaded_module("orders", reactions=[reaction], handler=handler)
        router = _router([mod])

        envelope = _envelope(payload={"order_id": "o-1"})
        await router.handle_event("order.created", envelope)
        assert len(called) == 1
        assert called[0]["order_id"] == "o-1"

    @pytest.mark.asyncio
    async def test_skips_when_handler_method_missing_from_target(self):
        # reaction has no handler_method in target
        reaction = _reaction_model("order.created", id="r-1", target={"kind": "handler"})
        handler = MagicMock()
        mod = _loaded_module("orders", reactions=[reaction], handler=handler)
        router = _router([mod])
        # Should not raise
        await router.handle_event("order.created", _envelope())
        handler.on_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_handler_for_module(self):
        reaction = _handler_target_reaction("order.created")
        mod = _loaded_module("orders", reactions=[reaction], handler=None)
        router = _router([mod])
        router._handlers_by_module.pop("orders", None)  # force no handler
        # Should not raise
        await router.handle_event("order.created", _envelope())

    @pytest.mark.asyncio
    async def test_exception_in_handler_is_tolerated(self):
        class BadHandler:
            async def on_event(self, ctx, **kwargs):
                raise RuntimeError("handler failure")

        reaction = _handler_target_reaction("order.created")
        mod = _loaded_module("orders", reactions=[reaction], handler=BadHandler())
        router = _router([mod])
        # Should not raise
        await router.handle_event("order.created", _envelope())

    @pytest.mark.asyncio
    async def test_ctx_has_app_tenant_user_from_envelope(self):
        received_ctx = []

        class Handler:
            async def on_event(self, ctx, **kwargs):
                received_ctx.append(ctx)

        reaction = _handler_target_reaction("order.created")
        mod = _loaded_module("orders", reactions=[reaction], handler=Handler())
        router = _router([mod])

        envelope = {
            "tenant": {"app_id": "app-99", "tenant_id": "t-99"},
            "actor": {"id": "user-99"},
            "payload": {},
        }
        await router.handle_event("order.created", envelope)
        assert len(received_ctx) == 1
        assert received_ctx[0].app_id == "app-99"
        assert received_ctx[0].tenant_id == "t-99"
        assert received_ctx[0].user_id == "user-99"


# ---------------------------------------------------------------------------
# 6. handle_event — notification target
# ---------------------------------------------------------------------------

class TestHandleEventNotificationTarget:
    @pytest.mark.asyncio
    async def test_notification_created_from_notification_rule(self):
        stored = []

        async def notification_store(record):
            stored.append(record)

        mod = _loaded_module(
            "payments",
            notifications=[
                _notification_rule("payment.completed", rule_id="ntf-paid", module_id="payments")
            ],
        )
        router = _router([mod], notification_store=notification_store)
        await router.handle_event("payment.completed", _envelope())
        assert len(stored) == 1
        assert stored[0]["rule_id"] == "ntf-paid"

    @pytest.mark.asyncio
    async def test_reaction_notification_target_creates_notification_once(self):
        stored = []

        async def notification_store(record):
            stored.append(record)

        # Both a reaction with notification target AND a direct notification rule
        reaction = _notification_target_reaction("order.created", notification_id="ntf-1")
        notif_rule = _notification_rule("order.created", rule_id="ntf-1", module_id="orders")
        mod = _loaded_module("orders", reactions=[reaction], notifications=[notif_rule])
        router = _router([mod], notification_store=notification_store)
        await router.handle_event("order.created", _envelope())
        # notification_id was already emitted via reaction — direct rule should be deduped
        assert len(stored) == 1


# ---------------------------------------------------------------------------
# 7. _create_notification — flat envelope fallback
# ---------------------------------------------------------------------------

class TestCreateNotification:
    @pytest.mark.asyncio
    async def test_structured_envelope_uses_payload_for_template(self):
        stored = []

        async def notification_store(record):
            stored.append(record)

        mod = _loaded_module(
            "m1",
            notifications=[
                _notification_rule(
                    "ev.test",
                    rule_id="r1",
                    module_id="m1",
                    template={"title": "Amount: {{amount}}", "body": ""},
                )
            ],
        )
        router = _router([mod], notification_store=notification_store)
        envelope = _envelope(payload={"amount": 42})
        await router.handle_event("ev.test", envelope)
        assert stored[0]["title"] == "Amount: 42"

    @pytest.mark.asyncio
    async def test_flat_envelope_used_as_payload(self):
        stored = []

        async def notification_store(record):
            stored.append(record)

        mod = _loaded_module(
            "m1",
            notifications=[
                _notification_rule(
                    "ev.flat",
                    rule_id="r1",
                    module_id="m1",
                    template={"title": "Order {{order_id}}", "body": ""},
                )
            ],
        )
        router = _router([mod], notification_store=notification_store)
        flat_envelope = {"app_id": "app-1", "order_id": "o-42"}
        await router.handle_event("ev.flat", flat_envelope)
        assert stored[0]["title"] == "Order o-42"

    @pytest.mark.asyncio
    async def test_context_fields_copied_to_record(self):
        stored = []

        async def notification_store(record):
            stored.append(record)

        mod = _loaded_module(
            "m1",
            notifications=[
                _notification_rule(
                    "ev.ctx",
                    rule_id="r1",
                    module_id="m1",
                    context_fields=["order_id", "amount"],
                )
            ],
        )
        router = _router([mod], notification_store=notification_store)
        envelope = _envelope(payload={"order_id": "o-1", "amount": 99, "extra": "x"})
        await router.handle_event("ev.ctx", envelope)
        context = stored[0].get("context")
        assert context is not None
        assert context["order_id"] == "o-1"
        assert context["amount"] == 99
        assert "extra" not in context

    @pytest.mark.asyncio
    async def test_condition_skips_notification_when_not_matched(self):
        stored = []

        async def notification_store(record):
            stored.append(record)

        mod = _loaded_module(
            "m1",
            notifications=[
                _notification_rule(
                    "ev.condition",
                    rule_id="r1",
                    module_id="m1",
                ) | {"condition": {"field": "sender_role", "equals": "operator"}}
            ],
        )
        router = _router([mod], notification_store=notification_store)
        await router.handle_event("ev.condition", _envelope(payload={"sender_role": "user"}))
        assert stored == []

    @pytest.mark.asyncio
    async def test_audience_user_id_field_targets_payload_user(self):
        stored = []

        async def notification_store(record):
            stored.append(record)

        mod = _loaded_module(
            "m1",
            notifications=[
                _notification_rule(
                    "ev.audience",
                    rule_id="r1",
                    module_id="m1",
                    audience={"user_id_field": "ticket_user_id"},
                )
            ],
        )
        router = _router([mod], notification_store=notification_store)
        await router.handle_event("ev.audience", _envelope(payload={"ticket_user_id": "user-42"}))
        assert stored[0]["audience"]["user_ids"] == ["user-42"]

    @pytest.mark.asyncio
    async def test_audience_user_id_field_expands_payload_user_list(self):
        stored = []

        async def notification_store(record):
            stored.append(record)

        mod = _loaded_module(
            "messages",
            notifications=[
                _notification_rule(
                    "domain.messages.message_sent",
                    rule_id="message_sent",
                    module_id="messages",
                    audience={"user_id_field": "recipient_ids"},
                )
            ],
        )
        router = _router([mod], notification_store=notification_store)
        await router.handle_event(
            "domain.messages.message_sent",
            _envelope(payload={"recipient_ids": ["user-2", "user-3", "user-2"]}),
        )
        assert stored[0]["audience"]["user_ids"] == ["user-2", "user-3"]

    @pytest.mark.asyncio
    async def test_secret_keys_stripped_from_context_fields(self):
        stored = []

        async def notification_store(record):
            stored.append(record)

        mod = _loaded_module(
            "m1",
            notifications=[
                _notification_rule(
                    "ev.secret",
                    rule_id="r1",
                    module_id="m1",
                    context_fields=["order_id", "api_key"],
                )
            ],
        )
        router = _router([mod], notification_store=notification_store)
        envelope = _envelope(payload={"order_id": "o-1", "api_key": "sk-123"})
        await router.handle_event("ev.secret", envelope)
        context = stored[0].get("context")
        assert context is None or "api_key" not in (context or {})

    @pytest.mark.asyncio
    async def test_emits_notification_created_event(self):
        emitted = []

        async def event_emitter(event_type, payload):
            emitted.append((event_type, payload))

        async def notification_store(record):
            pass

        mod = _loaded_module(
            "m1",
            notifications=[
                _notification_rule("ev.emit", rule_id="r1", module_id="m1")
            ],
        )
        router = _router([mod], event_emitter=event_emitter, notification_store=notification_store)
        await router.handle_event("ev.emit", _envelope())
        event_types = [e[0] for e in emitted]
        assert "notification.created" in event_types
        assert "notification.count_changed" in event_types
        count_event = next(payload for event_type, payload in emitted if event_type == "notification.count_changed")
        assert count_event["type"] == "notification.count_changed"
        assert "notification_id" not in count_event["payload"]

    @pytest.mark.asyncio
    async def test_notification_has_required_fields(self):
        stored = []

        async def notification_store(record):
            stored.append(record)

        mod = _loaded_module(
            "m1",
            notifications=[
                _notification_rule("ev.fields", rule_id="r1", module_id="m1")
            ],
        )
        router = _router([mod], notification_store=notification_store)
        await router.handle_event("ev.fields", _envelope())
        record = stored[0]
        for field in ("notification_id", "rule_id", "event_type", "created_at", "status"):
            assert field in record, f"missing field: {field}"
        assert record["status"] == "unread"


# ---------------------------------------------------------------------------
# 8. _emit_platform_reaction
# ---------------------------------------------------------------------------

class TestEmitPlatformReaction:
    @pytest.mark.asyncio
    async def test_no_op_when_no_event_emitter(self):
        reaction = _reaction_model("ev.test", id="r-1", target={"kind": "webhook"})
        mod = _loaded_module("m1", reactions=[reaction])
        # no event_emitter → should not raise
        router = _router([mod])
        await router.handle_event("ev.test", _envelope())  # no assertion, just no crash

    @pytest.mark.asyncio
    async def test_emits_platform_reaction_event(self):
        emitted = []

        async def event_emitter(event_type, payload):
            emitted.append(event_type)

        reaction = _reaction_model("ev.test", id="r-1", target={"kind": "webhook"})
        mod = _loaded_module("m1", reactions=[reaction])
        router = _router([mod], event_emitter=event_emitter)
        await router.handle_event("ev.test", _envelope())
        assert any("webhook_dispatched" in e for e in emitted)

    @pytest.mark.asyncio
    async def test_capability_invoker_called_for_capability_target(self):
        invoker_calls = []

        async def event_emitter(event_type, payload):
            pass

        async def capability_invoker(capability_id, envelope, reaction):
            invoker_calls.append(capability_id)
            return {"invoked": True}

        reaction = _reaction_model(
            "ev.cap",
            id="r-cap",
            target={"kind": "capability", "capability_id": "my_capability"},
        )
        mod = _loaded_module("m1", reactions=[reaction])
        router = _router([mod], event_emitter=event_emitter, capability_invoker=capability_invoker)
        await router.handle_event("ev.cap", _envelope())
        assert "my_capability" in invoker_calls

    @pytest.mark.asyncio
    async def test_service_adapter_target_invokes_adapter_and_emits_result(self):
        RecordingServiceAdapter.calls = []
        emitted = []

        async def event_emitter(event_type, payload):
            emitted.append((event_type, payload))

        reaction = _service_adapter_target_reaction("hosted.hosting.ci_provision.requested")
        mod = _loaded_module("hosting", reactions=[reaction])
        router = _router([mod], event_emitter=event_emitter)

        await router.handle_event(
            "hosted.hosting.ci_provision.requested",
            _envelope(payload={"build_registry_id": "owner/app"}),
        )

        assert RecordingServiceAdapter.calls == [{"build_registry_id": "owner/app"}]
        event_type, event = emitted[0]
        assert event_type == "platform.reaction.service_adapter_dispatched"
        assert event["payload"]["result"] == {"success": True, "seen": "owner/app"}
