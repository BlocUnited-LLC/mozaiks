from __future__ import annotations

from types import SimpleNamespace

import pytest

from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.tokens.guard import TokenUsageDecision, TokenUsageDenied
from tests.import_utils import import_module_directly

_bridge_mod = import_module_directly("mozaiksai.core.transport.workflow_bridge")
_ag2_mod = import_module_directly("mozaiksai.core.adapters.ag2_orchestration")

WorkflowBridgeMixin = _bridge_mod.WorkflowBridgeMixin


class _LiveRunResult:
    def __init__(self, *, status: object) -> None:
        self.status = status
        self.agent_name_by_id = {"agent-1": "ValueInterviewAgent"}
        self.wal = [
            {
                "event_type": "ag2.packet",
                "sender_id": "agent-1",
                "event_data": {"body": "I would start with founder validation."},
            }
        ]
        self.context_variables = {"target_user": "founders"}
        self.channel_id = "channel-1"
        self.close_reason = "awaiting_user_input"
        self.error = None


class _FakeLiveRun:
    def __init__(self, *, result: _LiveRunResult) -> None:
        self.result = result
        self.continued: list[dict[str, object]] = []

    async def continue_with_user_message(self, message: str, **kwargs):  # noqa: ANN003
        self.continued.append({"message": message, **kwargs})
        return self.result


class _FakePersistenceManager:
    def __init__(self) -> None:
        self.pending_lookups: list[dict[str, str]] = []
        self.pending_clears: list[dict[str, str]] = []
        self.pending_input_request: dict[str, str] | None = {"request_id": "req-1"}
        self.run_user_messages: list[dict[str, object]] = []
        self.run_assistant_messages: list[dict[str, object]] = []
        self.persisted_context: list[dict[str, object]] = []
        self.completed: list[dict[str, str]] = []

    async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
        self.pending_lookups.append(kwargs)
        return self.pending_input_request

    async def clear_pending_input_request(self, **kwargs):  # noqa: ANN003
        self.pending_clears.append(kwargs)

    async def append_run_user_message(self, **kwargs):  # noqa: ANN003
        self.run_user_messages.append(kwargs)

    async def append_run_assistant_message(self, **kwargs):  # noqa: ANN003
        self.run_assistant_messages.append(kwargs)

    async def persist_context_variables(self, **kwargs):  # noqa: ANN003
        self.persisted_context.append(kwargs)

    async def mark_chat_completed(self, chat_id: str, app_id: str) -> bool:
        self.completed.append({"chat_id": chat_id, "app_id": app_id})
        return True


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
        self._live_ag2_workflow_runs: dict[str, object] = {}

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
        extra_data: dict | None = None,
    ) -> None:
        self.errors.append(
            {
                "error_message": error_message,
                "error_code": error_code,
                "chat_id": chat_id,
                "extra_data": extra_data,
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

    def register_live_ag2_workflow_run(self, chat_id: str, live_run: object) -> None:
        self._live_ag2_workflow_runs[chat_id] = live_run

    def get_live_ag2_workflow_run(self, chat_id: str) -> object | None:
        return self._live_ag2_workflow_runs.get(chat_id)

    def pop_live_ag2_workflow_run(self, chat_id: str) -> object | None:
        return self._live_ag2_workflow_runs.pop(chat_id, None)


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
    assert persistence_manager.run_user_messages == [
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
            "content": "Proceed with the refinement.",
            "metadata": {"source": "workflow_user", "user_id": "user-1"},
        }
    ]
    assert transport.errors == []


@pytest.mark.asyncio
async def test_handle_user_input_from_api_persists_existing_session_user_reply_to_run_stream(monkeypatch) -> None:
    persistence_manager = _FakePersistenceManager()
    persistence_manager.pending_input_request = None
    transport = _DummyTransport(persistence_manager)
    transport._input_request_registries["chat-1"] = {"req-pending": object()}

    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _workflow_name: {})

    result = await transport.handle_user_input_from_api(
        chat_id="chat-1",
        user_id="user-1",
        workflow_name="ValueEngine",
        message="launch demand maybe",
        app_id="app-1",
    )

    assert result["status"] == "success"
    assert result["route"] == "existing_session"
    assert transport.submitted_inputs == [{"request_id": "req-pending", "user_input": "launch demand maybe"}]
    assert persistence_manager.run_user_messages == [
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
            "content": "launch demand maybe",
            "metadata": {"source": "workflow_user", "user_id": "user-1"},
        }
    ]
    assert transport.persisted_messages == [
        {
            "chat_id": "chat-1",
            "user_id": "user-1",
            "content": "launch demand maybe",
            "source": "http",
        }
    ]


@pytest.mark.asyncio
async def test_handle_user_input_from_api_prefers_live_ag2_network_channel(monkeypatch) -> None:
    persistence_manager = _FakePersistenceManager()
    persistence_manager.pending_input_request = None
    transport = _DummyTransport(persistence_manager)
    live_run = _FakeLiveRun(result=_LiveRunResult(status=RunStatus.PAUSED))
    transport.register_live_ag2_workflow_run("chat-1", live_run)

    async def _context_updates(**_kwargs):  # noqa: ANN003
        return {"target_user": "founders"}

    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _workflow_name: {})
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _context_updates)

    result = await transport.handle_user_input_from_api(
        chat_id="chat-1",
        user_id="user-1",
        workflow_name="ValueEngine",
        message="founders validating launch demand",
        app_id="app-1",
    )

    assert result["status"] == "success"
    assert result["route"] == "live_ag2_network"
    assert live_run.continued == [
        {
            "message": "founders validating launch demand",
            "context_updates": {"target_user": "founders"},
        }
    ]
    assert persistence_manager.run_user_messages == [
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
            "content": "founders validating launch demand",
            "metadata": {"source": "workflow_user", "user_id": "user-1"},
        }
    ]
    assert persistence_manager.run_assistant_messages == [
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
            "content": "I would start with founder validation.",
            "agent_name": "ValueInterviewAgent",
            "metadata": {"source": "ag2_network_wal", "channel_id": "channel-1"},
        }
    ]
    assert persistence_manager.persisted_context == [
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
            "variables": {"target_user": "founders"},
        }
    ]
    assert transport.get_live_ag2_workflow_run("chat-1") is live_run
    assert [entry["event"]["kind"] for entry in transport.sent_ui_events] == [
        "chat.text",
        "awaiting_reply",
        "run_complete",
    ]


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


@pytest.mark.asyncio
async def test_handle_user_input_from_api_surfaces_token_denial_with_structured_metadata(monkeypatch) -> None:
    """TokenUsageDenied must surface as INSUFFICIENT_TOKENS, not WORKFLOW_EXECUTION_FAILED."""
    persistence_manager = _FakePersistenceManager()
    transport = _DummyTransport(persistence_manager)

    class _DenyingAdapter:
        async def run(self, request):  # noqa: ANN001
            raise TokenUsageDenied(
                TokenUsageDecision(
                    allowed=False,
                    reason="insufficient_balance",
                    error_code="INSUFFICIENT_TOKENS",
                    wallet_id="ai_tokens",
                    balance=0,
                    required_tokens=1,
                    recovery_action="top_up_tokens",
                    billing_route="/billing",
                )
            )

        async def resume(self, request):  # noqa: ANN001
            return SimpleNamespace(status=SimpleNamespace(value="completed"))

    async def _noop_apply_context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _workflow_name: {})
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: _DenyingAdapter())
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _noop_apply_context_updates)

    result = await transport.handle_user_input_from_api(
        chat_id="chat-deny",
        user_id="user-1",
        workflow_name="AppGenerator",
        message="Run the AI workflow.",
        app_id="app-1",
    )

    assert result["status"] == "error"
    assert result["message"] == "Insufficient token balance"
    assert len(transport.errors) == 1
    err = transport.errors[0]
    assert err["error_code"] == "INSUFFICIENT_TOKENS"
    assert err["chat_id"] == "chat-deny"
    assert err["extra_data"] is not None
    assert err["extra_data"].get("error_code") == "INSUFFICIENT_TOKENS"
    assert err["extra_data"].get("wallet_id") == "ai_tokens"
    assert err["extra_data"].get("recovery_action") == "top_up_tokens"
    assert err["extra_data"].get("billing_route") == "/billing"

