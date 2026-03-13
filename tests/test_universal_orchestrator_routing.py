from __future__ import annotations

import pytest

from tests.import_utils import import_module_directly

_uo = import_module_directly("mozaiksai.core.orchestration.universal_orchestrator")
UniversalOrchestrator = _uo.UniversalOrchestrator


class _Collector:
    def __init__(self) -> None:
        self.calls = []

    async def handler(self, payload):  # type: ignore[no-untyped-def]
        self.calls.append(payload)
        return _uo.RouteResult(status="accepted", route="custom")


@pytest.mark.asyncio
async def test_structured_event_routes_directly() -> None:
    collector = _Collector()
    orchestrator = UniversalOrchestrator()
    orchestrator.configure_routes(event_routes={"ui.build_confirmed": "workflow.run:DecompositionGroupChat"})
    orchestrator.register_handler("workflow.run:DecompositionGroupChat", collector.handler)

    result = await orchestrator.handle_structured_event(
        {
            "event_type": "ui.build_confirmed",
            "app_id": "app-1",
            "user_id": "user-1",
            "chat_id": "chat-1",
            "message": "build it",
        }
    )

    assert result.status == "accepted"
    assert collector.calls
    assert collector.calls[0]["event_type"] == "ui.build_confirmed"


@pytest.mark.asyncio
async def test_free_text_event_uses_change_classifier() -> None:
    collector = _Collector()
    orchestrator = UniversalOrchestrator()
    orchestrator.configure_routes(change_type_routes={_uo.ChangeType.FOUNDATIONAL: "workflow.run:ValueEngineGroupChat"})
    orchestrator.register_handler("workflow.run:ValueEngine", collector.handler)

    result = await orchestrator.handle_free_text_event(
        {
            "text": "restart everything from scratch",
            "app_id": "app-1",
            "user_id": "user-1",
            "chat_id": "chat-2",
        }
    )

    assert result.status == "accepted"
    assert collector.calls
    assert collector.calls[0]["change_intent"]["change_type"] == "FOUNDATIONAL"
    assert collector.calls[0]["change_intent"]["target_workflow"] == "ValueEngine"
    assert result.route == "custom"


@pytest.mark.asyncio
async def test_structured_change_intent_routes_directly() -> None:
    collector = _Collector()
    orchestrator = UniversalOrchestrator()
    orchestrator.register_handler("workflow.run:ValueEngine", collector.handler)

    result = await orchestrator.handle_structured_event(
        {
            "app_id": "app-1",
            "user_id": "user-1",
            "chat_id": "chat-3",
            "change_intent": {
                "change_type": "FOUNDATIONAL",
                "change_scope": "foundational",
                "requires_appspec_revision": True,
                "requires_replan": True,
                "requires_new_iteration": True,
                "target_workflow": "ValueEngine",
                "rationale": "major product pivot",
                "confidence": 0.9,
            },
        }
    )

    assert result.status == "accepted"
    assert collector.calls
    assert collector.calls[0]["change_intent"]["target_workflow"] == "ValueEngine"


@pytest.mark.asyncio
async def test_unknown_structured_event_is_ignored() -> None:
    orchestrator = UniversalOrchestrator()
    result = await orchestrator.handle_structured_event({"event_type": "ui.unknown"})
    assert result.status == "ignored"
