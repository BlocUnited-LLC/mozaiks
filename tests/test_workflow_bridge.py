from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.import_utils import import_module_directly

_bridge_mod = import_module_directly("mozaiksai.core.transport.workflow_bridge")
_ag2_mod = import_module_directly("mozaiksai.core.adapters.ag2_orchestration")

WorkflowBridgeMixin = _bridge_mod.WorkflowBridgeMixin


class _FakePersistenceManager:
    def __init__(self) -> None:
        self.pending_lookups: list[dict[str, str]] = []
        self.pending_clears: list[dict[str, str]] = []
        self.pending_input_request: dict[str, str] | None = {"request_id": "req-1"}

    async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
        self.pending_lookups.append(kwargs)
        return self.pending_input_request

    async def clear_pending_input_request(self, **kwargs):  # noqa: ANN003
        self.pending_clears.append(kwargs)


class _FakeAdapter:
    def __init__(self) -> None:
        self.run_requests: list[object] = []
        self.resume_requests: list[object] = []

    async def run(self, request):  # noqa: ANN001
        self.run_requests.append(request)
        return SimpleNamespace(status=SimpleNamespace(value="completed"))

    async def resume(self, request):  # noqa: ANN001
        self.resume_requests.append(request)
        return SimpleNamespace(status=SimpleNamespace(value="completed"))


class _DummyTransport(WorkflowBridgeMixin):
    def __init__(self, persistence_manager: _FakePersistenceManager) -> None:
        self._input_request_registries = {}
        self._workflow_spawn_semaphore = None
        self._background_tasks = {}
        self.connections = {}
        self._derived_context_managers = {}
        self._persistence_manager = persistence_manager
        self.persisted_messages: list[dict[str, str | None]] = []
        self.errors: list[dict[str, str | None]] = []
        self.sent_ui_events: list[dict[str, object]] = []
        self.submitted_inputs: list[dict[str, str]] = []

    def _get_or_create_persistence_manager(self):
        return self._persistence_manager

    async def process_incoming_user_message(
        self,
        *,
        chat_id: str,
        user_id: str | None,
        content: str,
        source: str = "http",
    ) -> None:
        self.persisted_messages.append(
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "content": content,
                "source": source,
            }
        )

    async def send_error(
        self,
        error_message: str,
        error_code: str,
        chat_id: str,
    ) -> None:
        self.errors.append(
            {
                "error_message": error_message,
                "error_code": error_code,
                "chat_id": chat_id,
            }
        )

    async def send_event_to_ui(self, event: dict[str, object], chat_id: str | None = None) -> None:
        self.sent_ui_events.append({"chat_id": chat_id, "event": event})
        if event.get("kind") == "run_complete" and chat_id:
            self.connections.setdefault(chat_id, {})["ui_run_complete_sent"] = True

    async def submit_user_input(self, request_id: str, user_input: str) -> bool:
        self.submitted_inputs.append({"request_id": request_id, "user_input": user_input})
        return True

    def _build_resume_signal(self, chat_id: str, request_id: str) -> str:
        return f"resume:{chat_id}:{request_id}"


@pytest.mark.asyncio
async def test_handle_user_input_from_api_clears_persisted_pending_input_before_new_run(monkeypatch) -> None:
    persistence_manager = _FakePersistenceManager()
    adapter = _FakeAdapter()
    transport = _DummyTransport(persistence_manager)

    async def _noop_apply_context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _workflow_name: {})
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _noop_apply_context_updates)

    result = await transport.handle_user_input_from_api(
        chat_id="chat-1",
        user_id="user-1",
        workflow_name="AppGenerator",
        message="Proceed with the refinement.",
        app_id="app-1",
    )

    assert result["status"] == "success"
    assert persistence_manager.pending_lookups == [{"chat_id": "chat-1", "app_id": "app-1"}]
    assert persistence_manager.pending_clears == [{"chat_id": "chat-1", "app_id": "app-1"}]
    assert len(adapter.run_requests) == 1
    assert adapter.resume_requests == []
    assert transport.persisted_messages == [
        {
            "chat_id": "chat-1",
            "user_id": "user-1",
            "content": "Proceed with the refinement.",
            "source": "http",
        }
    ]
    assert transport.errors == []


@pytest.mark.asyncio
async def test_handle_user_input_from_api_emits_synthetic_run_complete_for_completed_run(monkeypatch) -> None:
    persistence_manager = _FakePersistenceManager()
    adapter = _FakeAdapter()
    transport = _DummyTransport(persistence_manager)
    transport.connections["chat-1"] = {}

    async def _noop_apply_context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _workflow_name: {})
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _noop_apply_context_updates)

    result = await transport.handle_user_input_from_api(
        chat_id="chat-1",
        user_id="user-1",
        workflow_name="AppGenerator",
        message="Proceed with the refinement.",
        app_id="app-1",
    )

    assert result["status"] == "success"
    assert transport.sent_ui_events == [
        {
            "chat_id": "chat-1",
            "event": {
                "kind": "run_complete",
                "agent": "AppGenerator",
                "chat_id": "chat-1",
                "status": 1,
                "reason": "finished",
                "awaiting_user_input": False,
                "metadata": {"source": "workflow_bridge.synthetic_completion"},
            },
        }
    ]


@pytest.mark.asyncio
async def test_handle_user_input_from_api_skips_synthetic_run_complete_when_already_sent(monkeypatch) -> None:
    persistence_manager = _FakePersistenceManager()
    adapter = _FakeAdapter()
    transport = _DummyTransport(persistence_manager)
    transport.connections["chat-1"] = {"ui_run_complete_sent": True}

    async def _noop_apply_context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _workflow_name: {})
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _noop_apply_context_updates)

    result = await transport.handle_user_input_from_api(
        chat_id="chat-1",
        user_id="user-1",
        workflow_name="AppGenerator",
        message="Proceed with the refinement.",
        app_id="app-1",
    )

    assert result["status"] == "success"
    assert transport.sent_ui_events == []


@pytest.mark.asyncio
async def test_handle_user_input_from_api_skips_synthetic_run_complete_when_pending_registry_exists(monkeypatch) -> None:
    persistence_manager = _FakePersistenceManager()
    persistence_manager.pending_input_request = None
    adapter = _FakeAdapter()
    transport = _DummyTransport(persistence_manager)
    transport.connections["chat-1"] = {"app_id": "app-1"}
    transport._input_request_registries["chat-1"] = {"req-pending": object()}

    async def _noop_apply_context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _workflow_name: {})
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _noop_apply_context_updates)

    result = await transport.handle_user_input_from_api(
        chat_id="chat-1",
        user_id="user-1",
        workflow_name="AppGenerator",
        message="Proceed with the refinement.",
        app_id="app-1",
    )

    assert result["status"] == "success"
    assert transport.sent_ui_events == []


@pytest.mark.asyncio
async def test_handle_user_input_from_api_skips_synthetic_run_complete_when_persisted_pending_input_exists(monkeypatch) -> None:
    persistence_manager = _FakePersistenceManager()
    persistence_manager.pending_input_request = {"request_id": "req-pending"}
    adapter = _FakeAdapter()
    transport = _DummyTransport(persistence_manager)
    transport.connections["chat-1"] = {"app_id": "app-1"}

    async def _noop_apply_context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _workflow_name: {})
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _noop_apply_context_updates)

    result = await transport.handle_user_input_from_api(
        chat_id="chat-1",
        user_id="user-1",
        workflow_name="AppGenerator",
        message="Proceed with the refinement.",
        app_id="app-1",
    )

    assert result["status"] == "success"
    assert transport.sent_ui_events == []

