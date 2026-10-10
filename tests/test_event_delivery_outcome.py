"""The bus receipt must reflect module reactions before a producer acknowledges them."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from mozaiksai.core.events.unified_event_dispatcher import (
    EventDispatchOutcome,
    UnifiedEventDispatcher,
)
from mozaiksai.core.runtime.composition.module_event_provenance import (
    ModuleEventRejection,
    ModuleReactionAudit,
)
from mozaiksai.core.runtime.composition.module_event_router import (
    ModuleEventDeliveryOutcome,
    ModuleEventRouter,
    required_module_reaction,
)
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from tests.module_authority_test_helpers import trusted_framework_authority

EVENT_TYPE = "domain.checkout.payment_succeeded"


class _Reaction:
    def __init__(
        self,
        reaction_id: str,
        *,
        permissions: list[str] | None = None,
        idempotency_key: str | None = None,
        target: dict[str, str] | None = None,
    ) -> None:
        self.event_type = EVENT_TYPE
        self.reaction_id = reaction_id
        self.permissions = permissions or []
        self.idempotency_key = idempotency_key
        self.target = target or {"kind": "handler", "handler_method": "on_payment"}

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "id": self.reaction_id,
            "target": self.target,
            "permissions": self.permissions,
            "idempotency_key": self.idempotency_key,
        }


def _module(
    name: str,
    handler: Any,
    reaction_id: str,
    *,
    permissions: list[str] | None = None,
    idempotency_key: str | None = None,
    target: dict[str, str] | None = None,
    notification_rules: list[dict[str, Any]] | None = None,
) -> Any:
    return SimpleNamespace(
        name=name,
        handler=handler,
        definition=None,
        manifests=SimpleNamespace(
            reactions=SimpleNamespace(
                reactions=[_Reaction(
                    reaction_id,
                    permissions=permissions,
                    idempotency_key=idempotency_key,
                    target=target,
                )]
            ),
            notifications=(
                SimpleNamespace(notifications=notification_rules)
                if notification_rules is not None else None
            ),
            events=None,
        ),
    )


@pytest.mark.asyncio
async def test_module_context_receives_mixed_reaction_results_with_matching_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Wallet:
        async def on_payment(self, _ctx: Any, *, payment_id: str) -> dict[str, Any]:
            return {"success": True, "payment_id": payment_id}

    class Campaign:
        async def on_payment(self, _ctx: Any, *, payment_id: str) -> dict[str, Any]:
            return {"success": False, "error_code": "BACKING_MISSING", "payment_id": payment_id}

    dispatcher = UnifiedEventDispatcher()
    router = ModuleEventRouter(
        [
            _module("wallet", Wallet(), "wallet.credit"),
            _module("campaign", Campaign(), "campaign.backing"),
        ]
    )
    audits: list[ModuleReactionAudit] = []

    async def capture_audit(audit: ModuleReactionAudit) -> None:
        audits.append(audit)

    monkeypatch.setattr(router, "_emit_reaction_audit", capture_audit)
    assert router.register(dispatcher) == 1

    receipts: list[EventDispatchOutcome | ModuleEventRejection | None] = []

    class Producer:
        async def succeed(self, ctx: Any) -> dict[str, bool]:
            receipts.append(await ctx.emit(EVENT_TYPE, {"payment_id": "pay-1"}))
            return {"success": True}

    executor = ModuleExecutor(event_emitter=dispatcher.emit)
    executor.register(
        "checkout",
        Producer(),
        action_method_map={"succeed": "succeed"},
        action_emits={"succeed": [EVENT_TYPE]},
    )
    action = await executor.execute(
        ModuleRequest(
            module="checkout",
            action="succeed",
            app_id="app-1",
            authority=trusted_framework_authority(),
        )
    )

    assert action.success is True  # A committed producer is not changed by fan-out failure.
    assert len(receipts) == 1
    receipt = receipts[0]
    assert isinstance(receipt, EventDispatchOutcome)
    assert receipt.success is False
    assert len(receipt.listeners) == 1
    assert receipt.listeners[0].status == "failed"
    assert isinstance(receipt.listeners[0].result, ModuleEventDeliveryOutcome)
    delivered = required_module_reaction(receipt, module_id="wallet", reaction_id="wallet.credit")
    assert delivered.status == "ok"
    assert delivered.delivered is True
    assert delivered.success is False  # A plain success result proves no durable effect.
    failed = required_module_reaction(receipt, module_id="campaign", reaction_id="campaign.backing")
    assert failed.status == "failed"
    assert failed.reason == "BACKING_MISSING"
    assert [(audit.reaction.reaction_id, audit.outcome) for audit in audits] == [
        ("wallet.credit", "ok"),
        ("campaign.backing", "failed"),
    ]
    assert [reaction.audit_id for reaction in receipt.listeners[0].result.reactions] == [
        audit.reaction_dispatch_id for audit in audits
    ]


@pytest.mark.asyncio
async def test_module_context_preserves_producer_event_id_on_retry() -> None:
    emitted: list[dict[str, Any]] = []

    async def capture(_event_type: str, envelope: dict[str, Any]) -> None:
        emitted.append(envelope)

    class Producer:
        async def succeed(self, ctx: Any) -> dict[str, bool]:
            await ctx.emit(
                EVENT_TYPE, {"payment_id": "pay-1"}, event_id="evt_stable_payment_1"
            )
            return {"success": True}

    executor = ModuleExecutor(event_emitter=capture)
    executor.register(
        "checkout", Producer(), action_method_map={"succeed": "succeed"},
        action_emits={"succeed": [EVENT_TYPE]},
    )
    request = ModuleRequest(
        module="checkout", action="succeed", app_id="app-1",
        authority=trusted_framework_authority(),
    )
    assert (await executor.execute(request)).success
    assert (await executor.execute(request)).success
    assert [item["id"] for item in emitted] == ["evt_stable_payment_1"] * 2


@pytest.mark.asyncio
async def test_required_reaction_fails_closed_for_absent_skipped_and_raised_handlers() -> None:
    dispatcher = UnifiedEventDispatcher()
    absent = await dispatcher.emit(EVENT_TYPE, {"payment_id": "pay-1"})
    assert absent.success is False
    assert required_module_reaction(absent, module_id="wallet", reaction_id="wallet.credit").status == "missing"

    class Skipped:
        async def on_payment(self, _ctx: Any, *, payment_id: str) -> None:
            raise AssertionError(f"permission-denied reaction ran for {payment_id}")

    skipped_router = ModuleEventRouter(
        [_module("wallet", Skipped(), "wallet.credit", permissions=["wallet.credit"])]
    )
    skipped_dispatcher = UnifiedEventDispatcher()
    skipped_router.register(skipped_dispatcher)
    skipped = await skipped_dispatcher.emit(EVENT_TYPE, {"payload": {"payment_id": "pay-1"}})
    assert required_module_reaction(
        skipped, module_id="wallet", reaction_id="wallet.credit"
    ).status == "skipped"

    class Raised:
        async def on_payment(self, _ctx: Any, *, payment_id: str) -> None:
            raise RuntimeError(f"wallet failed for {payment_id}")

    raised_dispatcher = UnifiedEventDispatcher()
    raised_router = ModuleEventRouter([_module("wallet", Raised(), "wallet.credit")])
    raised_router.register(raised_dispatcher)
    raised = await raised_dispatcher.emit(EVENT_TYPE, {"payload": {"payment_id": "pay-1"}})
    assert raised.success is False
    assert required_module_reaction(raised, module_id="wallet", reaction_id="wallet.credit").status == "failed"
    assert required_module_reaction(raised, module_id="wallet", reaction_id="wallet.other").status == "missing"


@pytest.mark.asyncio
async def test_dispatcher_reports_sync_async_failure_and_rejection_without_raising() -> None:
    dispatcher = UnifiedEventDispatcher()

    def accepted(_payload: dict[str, Any]) -> dict[str, bool]:
        return {"success": True}

    def returned_failure(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"success": False, "error_code": "FAILED"}

    def raised_sync(_payload: dict[str, Any]) -> None:
        raise ValueError("listener failed synchronously")

    async def raised_failure(_payload: dict[str, Any]) -> None:
        raise RuntimeError("listener failed")

    async def cancelled(_payload: dict[str, Any]) -> None:
        raise asyncio.CancelledError

    dispatcher.register_handler(EVENT_TYPE, accepted)
    dispatcher.register_handler(EVENT_TYPE, returned_failure)
    dispatcher.register_handler(EVENT_TYPE, raised_sync)
    dispatcher.register_handler(EVENT_TYPE, raised_failure)
    dispatcher.register_handler(EVENT_TYPE, cancelled)
    receipt = await dispatcher.emit(EVENT_TYPE, {})

    assert [listener.status for listener in receipt.listeners] == [
        "ok", "failed", "failed", "failed", "failed"
    ]
    assert receipt.listeners[0].result is None  # Arbitrary callback output stays private.
    assert receipt.listeners[1].result is None
    assert receipt.listeners[2].error_type == "ValueError"
    assert receipt.listeners[3].error_type == "RuntimeError"
    assert receipt.listeners[4].error_type == "CancelledError"
    assert receipt.success is False
    assert required_module_reaction(receipt, module_id="wallet", reaction_id="wallet.credit").status == "missing"

    rejection = ModuleEventRejection(
        event_id="evt-1",
        event_type=EVENT_TYPE,
        category="schema_invalid",
        reason="invalid schema",
    )
    assert required_module_reaction(
        rejection, module_id="wallet", reaction_id="wallet.credit"
    ).status == "failed"


@pytest.mark.asyncio
async def test_returned_failure_does_not_suppress_same_router_retry() -> None:
    attempts = 0

    class Wallet:
        async def on_payment(self, _ctx: Any, *, payment_id: str) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            return {"success": attempts > 1, "payment_id": payment_id}

    dispatcher = UnifiedEventDispatcher()
    router = ModuleEventRouter([
        _module("wallet", Wallet(), "wallet.credit", idempotency_key="payment_id")
    ])
    router.register(dispatcher)
    envelope = {
        "id": "evt-pay-1",
        "type": EVENT_TYPE,
        "tenant": {"app_id": "app-1", "tenant_id": "tenant-1"},
        "payload": {"payment_id": "pay-1"},
    }

    first = await dispatcher.emit(EVENT_TYPE, envelope)
    second = await dispatcher.emit(EVENT_TYPE, envelope)

    assert required_module_reaction(first, module_id="wallet", reaction_id="wallet.credit").status == "failed"
    assert required_module_reaction(second, module_id="wallet", reaction_id="wallet.credit").status == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_bare_false_handler_is_failed_and_retryable_with_matching_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    class Wallet:
        async def on_payment(self, _ctx: Any, *, payment_id: str) -> bool:
            nonlocal attempts
            attempts += 1
            assert payment_id == "pay-1"
            return attempts > 1

    router = ModuleEventRouter([
        _module("wallet", Wallet(), "wallet.credit", idempotency_key="payment_id")
    ])
    audits: list[ModuleReactionAudit] = []

    async def capture_audit(audit: ModuleReactionAudit) -> None:
        audits.append(audit)

    monkeypatch.setattr(router, "_emit_reaction_audit", capture_audit)
    dispatcher = UnifiedEventDispatcher()
    router.register(dispatcher)
    envelope = {
        "id": "evt-pay-1", "type": EVENT_TYPE,
        "tenant": {"app_id": "app-1", "tenant_id": "tenant-1"},
        "payload": {"payment_id": "pay-1"},
    }

    first = await dispatcher.emit(EVENT_TYPE, envelope)
    second = await dispatcher.emit(EVENT_TYPE, envelope)

    assert required_module_reaction(first, module_id="wallet", reaction_id="wallet.credit").status == "failed"
    assert required_module_reaction(second, module_id="wallet", reaction_id="wallet.credit").status == "ok"
    assert [audit.outcome for audit in audits] == ["failed", "ok"]
    assert attempts == 2


@pytest.mark.asyncio
async def test_bare_false_service_adapter_and_capability_are_failed_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    router = ModuleEventRouter([
        _module(
            "billing", object(), "billing.post",
            idempotency_key="payment_id",
            target={"kind": "service_adapter", "adapter": "test:Adapter", "adapter_method": "post"},
        )
    ])

    async def adapter(*_args: Any, **_kwargs: Any) -> bool:
        nonlocal attempts
        attempts += 1
        return attempts > 1

    monkeypatch.setattr(router, "_dispatch_service_adapter", adapter)
    dispatcher = UnifiedEventDispatcher()
    router.register(dispatcher)
    envelope = {
        "id": "evt-pay-1", "type": EVENT_TYPE,
        "tenant": {"app_id": "app-1", "tenant_id": "tenant-1"},
        "payload": {"payment_id": "pay-1"},
    }
    first = await dispatcher.emit(EVENT_TYPE, envelope)
    second = await dispatcher.emit(EVENT_TYPE, envelope)
    assert required_module_reaction(first, module_id="billing", reaction_id="billing.post").status == "failed"
    assert required_module_reaction(second, module_id="billing", reaction_id="billing.post").status == "ok"
    assert attempts == 2

    capability_router = ModuleEventRouter(
        [_module(
            "billing", object(), "billing.capability", idempotency_key="payment_id",
            target={"kind": "capability", "capability_id": "billing.post"},
        )],
        capability_invoker=lambda *_args: False,
    )
    capability_dispatcher = UnifiedEventDispatcher()
    capability_router.register(capability_dispatcher)
    capability = await capability_dispatcher.emit(EVENT_TYPE, envelope)
    assert required_module_reaction(
        capability, module_id="billing", reaction_id="billing.capability"
    ).status == "failed"


@pytest.mark.asyncio
async def test_partial_required_reactions_converge_with_verified_same_router_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wallet_calls = 0
    campaign_calls = 0

    class Wallet:
        async def on_payment(self, _ctx: Any, *, payment_id: str) -> bool:
            nonlocal wallet_calls
            wallet_calls += 1
            return payment_id == "pay-1"

    class Campaign:
        async def on_payment(self, _ctx: Any, *, payment_id: str) -> bool:
            nonlocal campaign_calls
            campaign_calls += 1
            return campaign_calls > 1 and payment_id == "pay-1"

    router = ModuleEventRouter([
        _module("wallet", Wallet(), "wallet.credit", idempotency_key="payment_id"),
        _module("campaign", Campaign(), "campaign.backing", idempotency_key="payment_id"),
    ])
    audits: list[ModuleReactionAudit] = []

    async def capture_audit(audit: ModuleReactionAudit) -> None:
        audits.append(audit)

    monkeypatch.setattr(router, "_emit_reaction_audit", capture_audit)
    dispatcher = UnifiedEventDispatcher()
    router.register(dispatcher)
    envelope = {
        "id": "evt-pay-1", "type": EVENT_TYPE,
        "tenant": {"app_id": "app-1", "tenant_id": "tenant-1"},
        "payload": {"payment_id": "pay-1"},
    }

    first = await dispatcher.emit(EVENT_TYPE, envelope)
    second = await dispatcher.emit(EVENT_TYPE, envelope)

    assert required_module_reaction(first, module_id="wallet", reaction_id="wallet.credit").status == "ok"
    assert required_module_reaction(first, module_id="campaign", reaction_id="campaign.backing").status == "failed"
    assert required_module_reaction(second, module_id="wallet", reaction_id="wallet.credit").status == "completed"
    assert required_module_reaction(second, module_id="campaign", reaction_id="campaign.backing").status == "ok"
    assert second.success is True
    assert (wallet_calls, campaign_calls) == (1, 2)
    assert [(audit.reaction.reaction_id, audit.outcome) for audit in audits] == [
        ("wallet.credit", "ok"), ("campaign.backing", "failed"),
        ("wallet.credit", "skipped"), ("campaign.backing", "ok"),
    ]
    assert audits[2].reason == "idempotent reaction already completed"
    assert second.listeners[0].result.reactions[0].audit_id == audits[2].reaction_dispatch_id


@pytest.mark.asyncio
async def test_raising_capability_is_failed_and_retried_on_same_router() -> None:
    calls = 0

    async def invoke(*_args: Any) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary capability failure")
        return True

    router = ModuleEventRouter(
        [_module(
            "wallet", object(), "wallet.credit", idempotency_key="payment_id",
            target={"kind": "capability", "capability_id": "wallet.credit"},
        )],
        capability_invoker=invoke,
    )
    dispatcher = UnifiedEventDispatcher()
    router.register(dispatcher)
    envelope = {
        "id": "evt-pay-1", "type": EVENT_TYPE,
        "tenant": {"app_id": "app-1", "tenant_id": "tenant-1"},
        "payload": {"payment_id": "pay-1"},
    }
    first = await dispatcher.emit(EVENT_TYPE, envelope)
    second = await dispatcher.emit(EVENT_TYPE, envelope)

    failed = required_module_reaction(first, module_id="wallet", reaction_id="wallet.credit")
    assert failed.status == "failed"
    assert failed.reason == "RuntimeError"
    assert required_module_reaction(second, module_id="wallet", reaction_id="wallet.credit").status == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_skipped_explicit_notification_is_not_recreated_by_implicit_rules() -> None:
    stored: list[dict[str, Any]] = []
    module = _module(
        "notices", object(), "notify.owner",
        permissions=["notices.send"],
        target={"kind": "notification", "notification_id": "owner_notice"},
        notification_rules=[{
            "id": "owner_notice", "event_type": EVENT_TYPE, "module_id": "notices",
            "channels": ["in_app"], "template": {"title": "Notice", "body": ""},
        }],
    )
    router = ModuleEventRouter([module], notification_store=stored.append)
    dispatcher = UnifiedEventDispatcher()
    router.register(dispatcher)

    receipt = await dispatcher.emit(EVENT_TYPE, {
        "id": "evt-1", "type": EVENT_TYPE,
        "tenant": {"app_id": "app-1", "tenant_id": "tenant-1"},
        "payload": {"payment_id": "pay-1"},
    })

    assert required_module_reaction(
        receipt, module_id="notices", reaction_id="notify.owner"
    ).status == "skipped"
    assert stored == []
