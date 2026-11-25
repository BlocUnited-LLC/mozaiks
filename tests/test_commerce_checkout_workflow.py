"""The checkout integration may launch its existing workflow from its own reaction."""

from pathlib import Path
from shutil import copytree
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_validation import _write_files_to_dir
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_managed_capability_templates,
)
from mozaiksai.core.account import account_data_registry
from mozaiksai.core.events.unified_event_dispatcher import UnifiedEventDispatcher
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.hosts import platform
from tests.test_workflow_trigger_guard import _guard


@pytest.mark.asyncio
async def test_receiver_owned_checkout_reaction_starts_existing_workflow_once(
    tmp_path, monkeypatch
):
    event_type = "domain.commerce.checkout.requested"
    capability_id = "mozaikspay.checkout.create_session"
    reaction_id = "commerce_checkout_requested_payment_facade"
    # The existing workflow exposes the capability and trigger. The receiver's reaction subscribes to the event.
    workflows = tmp_path / "workflows"
    workflow = workflows / "RuntimeSmoke"
    copytree(Path(__file__).resolve().parents[1] / "factory_app/workflows/RuntimeSmoke", workflow)
    orchestrator = workflow / "orchestrator.yaml"
    definition = yaml.safe_load(orchestrator.read_text(encoding="utf-8"))
    definition["triggers"] = [
        {"type": "event", "event": event_type, "capability_id": capability_id}
    ]
    orchestrator.write_text(yaml.safe_dump(definition), encoding="utf-8")
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workflows))
    monkeypatch.setattr(account_data_registry, "_handlers", {})

    app = tmp_path / "app"
    pack_files = resolve_managed_capability_templates(
        [{"id": "commerce", "capability_source": "operator_pack"}]
    )
    files = {entry["filename"]: entry["content"] for entry in pack_files}
    files["modules/checkout_facade/module.yaml"] = yaml.safe_dump(
        {
            "schema_version": "mozaiks.module.v1",
            "module": {"id": "checkout_facade", "handler": "backend.handler:Receiver"},
            "capabilities": [
                {
                    "capability_id": capability_id,
                    "kind": "workflow",
                    "target": workflow.name,
                    "title": "Checkout",
                }
            ],
        }
    )
    files["modules/checkout_facade/backend/handler.py"] = "class Receiver:\n    pass\n"
    files["modules/checkout_facade/contracts/reactions.yaml"] = yaml.safe_dump(
        {
            "schema_version": "mozaiks.reactions.v1",
            "reactions": [
                {
                    "id": reaction_id,
                    "event_type": event_type,
                    "target": {"kind": "capability", "capability_id": capability_id},
                }
            ],
        }
    )
    _write_files_to_dir(app, files)
    loader = ModuleLoader(str(app))
    routes = platform._load_workflow_capability_routes(app)
    guard = _guard()
    create_session = AsyncMock(return_value="checkout-session")
    statuses = []

    async def invoke(capability, event, subscription):
        result = await platform._invoke_workflow_capability(
            capability_id=capability,
            source_event=event,
            subscription=subscription,
            routes=routes,
            trigger_guard=guard,
            create_session=create_session,
            auto_start=False,
        )
        statuses.append(result["status"])
        return result

    dispatcher = UnifiedEventDispatcher()
    router = ModuleEventRouter(
        [loader.load("commerce"), loader.load("checkout_facade")], capability_invoker=invoke
    )
    router.register(dispatcher)
    event = {
        "id": "checkout-event",
        "type": event_type,
        "tenant": {"app_id": "shop", "tenant_id": "tenant"},
        "actor": {"type": "user", "id": "buyer"},
        "payload": {
            "checkout_id": "checkout-1",
            "order_id": "order-1",
            "actor_id": "buyer",
            "payment_provider": "mozaikspay",
            "amount": 100,
            "currency": "USD",
        },
    }
    await dispatcher.emit(event_type, event)
    await dispatcher.emit(event_type, event)

    assert statuses == ["created", "replay_suppressed"]
    create_session.assert_awaited_once()
    request = create_session.await_args.kwargs
    assert request["workflow_id"] == workflow.name
    assert request["trigger_meta"]["subscription_id"] == reaction_id
    assert request["trigger_meta"]["module_id"] == "checkout_facade"
