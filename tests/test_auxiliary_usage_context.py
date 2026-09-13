from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from mozaiksai.control_plane.contracts import CodingWorkerRequest
from mozaiksai.control_plane.implementations.refinement_router import RefinementTriggerRouteResolver
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.tokens.manager import TokenManager
from mozaiksai.core.usage.context import (
    AuxiliaryUsageContext,
    UsageReceiptScope,
    resolve_auxiliary_usage_context,
)
from mozaiksai.core.usage.ledger import RuntimeUsageLedger


@pytest.mark.parametrize("field", ["app_id", "user_id"])
@pytest.mark.parametrize("value", [None, "", "  ", 12])
def test_auxiliary_owner_is_required_and_typed(field, value):
    with pytest.raises(ValidationError):
        AuxiliaryUsageContext(**{"app_id": "host", "user_id": "owner", field: value})


def test_context_rejects_untrusted_extra_fields_and_binding_target_mismatch():
    with pytest.raises(ValidationError):
        AuxiliaryUsageContext(app_id="host", user_id="owner", build_id="caller-build")
    binding = RunBuildBinding(
        target_app_id="target", build_registry_id="registry", build_id="revision", phase="refinement",
    )
    with pytest.raises(ValueError, match="target"):
        resolve_auxiliary_usage_context(
            app_id="host", user_id="owner", target_app_id="another-target", run_build_binding=binding,
        )
    for field in ("app_id", "user_id"):
        with pytest.raises(ValueError, match="owner"):
            resolve_auxiliary_usage_context(
                app_id="host", user_id="owner",
                context=AuxiliaryUsageContext(**{"app_id": "host", "user_id": "owner", field: "forged"}),
            )


@pytest.mark.parametrize("kind,fields", [
    ("workflow", {"chat_id": None, "workflow_name": "Actual"}),
    ("workflow", {"chat_id": "actual", "workflow_name": None}),
    ("auxiliary", {"agent_name": None}),
    ("invented", {"agent_name": "Agent"}),
])
def test_receipt_discriminator_does_not_weaken_workflow_identity(kind, fields):
    with pytest.raises(ValidationError):
        UsageReceiptScope(execution_kind=kind, app_id="host", user_id="owner", **fields)


def test_payload_cannot_supply_usage_context_or_override_owner():
    resolver = RefinementTriggerRouteResolver()
    context = AuxiliaryUsageContext(app_id="host", user_id="owner", tenant_id="tenant")
    payload = {"refinement_request": {
        "build_family": "app_bundle", "app_id": "forged", "user_id": "forged",
        "usage_context": {"app_id": "forged", "user_id": "forged", "chat_id": "fake"},
        "extra": {"build_id": "old-build", "tenant_id": "forged"},
    }}
    request = resolver.request_from_payload(
        payload=payload, app_id="host", user_id="owner", target_app_id="target", usage_context=context,
    )
    assert request.usage_context is context
    assert request.app_id == "host" and request.user_id == "owner"
    assert "usage_context" not in request.model_dump()
    assert context.chat_id is context.workflow_name is context.run_build_binding is None
    unbound = resolver.request_from_payload(payload=payload, app_id="host", user_id="owner")
    assert unbound.usage_context is None
    coding = CodingWorkerRequest(
        app_id="host", user_id="owner", build_family="app_bundle", change_class="patch", usage_context=context,
    )
    assert "usage_context" not in coding.model_dump()


@pytest.mark.asyncio
async def test_runner_rejects_untyped_scope_before_agent_creation():
    from pydantic import BaseModel
    factory = AsyncMock()
    with pytest.raises(TypeError, match="AuxiliaryUsageContext"):
        await AG2StructuredAgentRunner(agent_factory=factory).run(
            agent_name="Agent", system_prompt="system", user_prompt="user", llm_config={},
            response_schema=BaseModel, usage_context={"app_id": "host", "user_id": "owner"},
        )
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"user_id": None}, {"event_id": None}, {"agent_name": None}, {"execution_kind": "workflow"}])
async def test_ledger_rejects_incomplete_auxiliary_receipt_without_db_access(monkeypatch, changes):
    ledger = RuntimeUsageLedger()
    coll = AsyncMock()
    monkeypatch.setattr(ledger, "_coll", coll)
    await ledger.record_usage_delta({
        "execution_kind": "auxiliary", "event_id": "event", "app_id": "host", "user_id": "owner",
        "agent_name": "Agent", "chat_id": None, "workflow_name": None, "total_tokens": 1, **changes,
    })
    coll.assert_not_awaited()


@pytest.mark.asyncio
async def test_collector_rejects_incomplete_auxiliary_scope(monkeypatch):
    from mozaiksai.core.events import unified_event_dispatcher
    dispatcher = AsyncMock()
    monkeypatch.setattr(unified_event_dispatcher, "get_event_dispatcher", lambda: dispatcher)
    monkeypatch.setenv("USAGE_EVENTS_ENABLED", "true")
    await TokenManager.emit_usage_delta(
        execution_kind="auxiliary", app_id="host", user_id="owner", chat_id=None,
        workflow_name=None, agent_name=None, total_tokens=1,
    )
    dispatcher.emit.assert_not_awaited()
