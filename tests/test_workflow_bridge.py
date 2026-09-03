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

    async def persist_server_owned_session_fields(self, **kwargs):  # noqa: ANN003
        return None

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
            "workflow_name": "ValueEngine",
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
async def test_live_ag2_context_persistence_failure_propagates(monkeypatch) -> None:
    class _FailingPersistenceManager(_FakePersistenceManager):
        async def persist_context_variables(self, **kwargs):  # noqa: ANN003
            self.persisted_context.append(kwargs)
            raise RuntimeError("context update failed")

    persistence_manager = _FailingPersistenceManager()
    transport = _DummyTransport(persistence_manager)
    live_run = _FakeLiveRun(result=_LiveRunResult(status=RunStatus.PAUSED))

    async def _context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _context_updates)

    with pytest.raises(RuntimeError, match="context update failed"):
        await transport._continue_live_ag2_workflow_run(
            live_run=live_run,
            chat_id="chat-1",
            user_id="user-1",
            workflow_name="ValueEngine",
            message="continue",
            app_id="app-1",
        )

    assert persistence_manager.persisted_context == [
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
            "workflow_name": "ValueEngine",
            "variables": {"target_user": "founders"},
        }
    ]


@pytest.mark.asyncio
async def test_user_text_context_fetch_failure_propagates() -> None:
    class _FailingFetchPersistenceManager(_FakePersistenceManager):
        async def fetch_chat_session_extra_context(self, **_kwargs):  # noqa: ANN003
            raise RuntimeError("context fetch failed")

    transport = _DummyTransport(_FailingFetchPersistenceManager())

    with pytest.raises(RuntimeError, match="context fetch failed"):
        await transport._apply_user_text_context_updates(
            chat_id="chat-1",
            workflow_name="ValueEngine",
            app_id="app-1",
            user_input="continue",
        )


@pytest.mark.asyncio
async def test_user_text_context_update_failure_propagates() -> None:
    class _FailingUpdatePersistenceManager(_FakePersistenceManager):
        async def persist_context_variables(self, **kwargs):  # noqa: ANN003
            self.persisted_context.append(kwargs)
            raise RuntimeError("context update failed")

    class _DerivedContextManager:
        def apply_user_text(self, _candidate: str) -> dict[str, str]:
            return {"target_user": "founders"}

    persistence_manager = _FailingUpdatePersistenceManager()
    transport = _DummyTransport(persistence_manager)
    transport._derived_context_managers["chat-1"] = _DerivedContextManager()

    with pytest.raises(RuntimeError, match="context update failed"):
        await transport._apply_user_text_context_updates(
            chat_id="chat-1",
            workflow_name="ValueEngine",
            app_id="app-1",
            user_input="founders",
        )

    assert persistence_manager.persisted_context == [
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
            "workflow_name": "ValueEngine",
            "variables": {"target_user": "founders"},
        }
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


@pytest.mark.asyncio
async def test_required_on_fail_hook_failure_keeps_original_failure(monkeypatch, caplog) -> None:
    """A required on_fail persistence hook raising must not change the
    outcome: the original workflow failure remains the reported failure, the
    lifecycle persistence failure is surfaced loudly with the original
    failure class as diagnostic context, and no recursive on_fail occurs."""
    persistence_manager = _FakePersistenceManager()
    transport = _DummyTransport(persistence_manager)

    class _ExplodingAdapter:
        async def run(self, request):  # noqa: ANN001
            raise RuntimeError("workflow blew up")

        async def resume(self, request):  # noqa: ANN001
            raise RuntimeError("workflow blew up")

    on_fail_calls: list[dict] = []

    async def _required_on_fail(**kwargs):  # noqa: ANN003
        on_fail_calls.append(kwargs)
        raise RuntimeError("required lifecycle persistence unavailable")

    async def _noop_apply_context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(
        _bridge_mod,
        "get_workflow_lifecycle_hooks",
        lambda _workflow_name: {"on_fail": _required_on_fail},
    )
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: _ExplodingAdapter())
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _noop_apply_context_updates)

    result = await transport.handle_user_input_from_api(
        chat_id="chat-1",
        user_id="user-1",
        workflow_name="TestFlow",
        message="hello",
        app_id="app-1",
    )

    # Original failure remains the failure; on_fail dispatch was awaited
    # exactly once (no recursion, no detached task).
    assert result["status"] == "error"
    assert len(on_fail_calls) == 1
    assert any(
        "LIFECYCLE_FAILURE_PERSISTENCE_FAILED" in record.message
        and "RuntimeError" in record.getMessage()
        for record in caplog.records
    )
    assert any(e.get("error_code") == "WORKFLOW_EXECUTION_FAILED" for e in transport.errors)


@pytest.mark.asyncio
async def test_on_fail_hook_success_still_reports_original_failure(monkeypatch) -> None:
    """Ordinary workflow failure with a healthy awaited on_fail dispatch:
    the hook runs to completion before the error result returns."""
    persistence_manager = _FakePersistenceManager()
    transport = _DummyTransport(persistence_manager)

    class _ExplodingAdapter:
        async def run(self, request):  # noqa: ANN001
            raise RuntimeError("workflow blew up")

        async def resume(self, request):  # noqa: ANN001
            raise RuntimeError("workflow blew up")

    on_fail_calls: list[dict] = []

    async def _on_fail(**kwargs):  # noqa: ANN003
        on_fail_calls.append(kwargs)
        return "outbox_failed"

    async def _noop_apply_context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(
        _bridge_mod,
        "get_workflow_lifecycle_hooks",
        lambda _workflow_name: {"on_fail": _on_fail},
    )
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: _ExplodingAdapter())
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _noop_apply_context_updates)

    result = await transport.handle_user_input_from_api(
        chat_id="chat-1",
        user_id="user-1",
        workflow_name="TestFlow",
        message="hello",
        app_id="app-1",
    )
    assert result["status"] == "error"
    assert len(on_fail_calls) == 1


class _StoringPersistenceManager(_FakePersistenceManager):
    """Fake PM that actually persists server-owned session fields."""

    def __init__(self) -> None:
        super().__init__()
        self.session: dict[str, object] = {}

    async def persist_server_owned_session_fields(self, **kwargs):  # noqa: ANN003
        self.session.update(kwargs.get("fields") or {})

    async def fetch_chat_session_extra_context(self, **kwargs):  # noqa: ANN003
        return dict(self.session)


class _AlwaysFailingAdapter:
    async def run(self, request):  # noqa: ANN001
        raise RuntimeError("workflow blew up")

    async def resume(self, request):  # noqa: ANN001
        raise RuntimeError("workflow blew up")


@pytest.mark.asyncio
async def test_two_failed_runs_in_one_chat_have_distinct_failure_identity(monkeypatch) -> None:
    """Codex 1's failure-identity attack, driven through the real production
    outer error path.

    Run A and Run B are distinct workflow runs in the same chat, each ending
    in a terminal ordinary failure. The on_fail lifecycle dispatch must carry
    each run's exact server-owned immutable workflow_run_id — previously both
    dispatches received only execution_id=chat_id / chat_id with no
    workflow_run_id, so two distinct failed runs shared build.failed event
    and idempotency authority.
    """
    persistence_manager = _StoringPersistenceManager()
    transport = _DummyTransport(persistence_manager)

    on_fail_calls: list[dict] = []

    async def _on_fail(**kwargs):  # noqa: ANN003
        on_fail_calls.append(dict(kwargs))
        return "outbox_failed"

    async def _noop_apply_context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(
        _bridge_mod,
        "get_workflow_lifecycle_hooks",
        lambda _workflow_name: {"on_fail": _on_fail},
    )
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: _AlwaysFailingAdapter())
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _noop_apply_context_updates)

    # Run A: fresh run, terminal ordinary failure.
    result_a = await transport.handle_user_input_from_api(
        chat_id="same-chat", user_id="user-1", workflow_name="TestFlow",
        message="run A", app_id="app-1",
    )
    run_a_id = persistence_manager.session.get("workflow_run_id")

    # Run B: fresh run, same chat/workflow, terminal ordinary failure.
    result_b = await transport.handle_user_input_from_api(
        chat_id="same-chat", user_id="user-1", workflow_name="TestFlow",
        message="run B", app_id="app-1",
    )
    run_b_id = persistence_manager.session.get("workflow_run_id")

    assert result_a["status"] == "error"
    assert result_b["status"] == "error"
    assert len(on_fail_calls) == 2, "exactly one on_fail dispatch per terminal failure"
    assert run_a_id and run_b_id and run_a_id != run_b_id

    dispatched_run_ids = [call.get("workflow_run_id") for call in on_fail_calls]
    assert dispatched_run_ids == [run_a_id, run_b_id], (
        "on_fail must carry each run's exact immutable workflow_run_id; got "
        f"{dispatched_run_ids} (chat_id={on_fail_calls[0].get('chat_id')!r}, "
        f"execution_id={on_fail_calls[0].get('execution_id')!r})"
    )
    # The failure identity must never be substituted with chat identity.
    for call in on_fail_calls:
        assert call.get("workflow_run_id") != call.get("chat_id")


def _failing_env(monkeypatch, transport, on_fail_calls, hooks=None):
    async def _on_fail(**kwargs):  # noqa: ANN003
        on_fail_calls.append(dict(kwargs))
        return "outbox_failed"

    async def _noop_apply_context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(
        _bridge_mod,
        "get_workflow_lifecycle_hooks",
        lambda _workflow_name: hooks if hooks is not None else {"on_fail": _on_fail},
    )
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: _AlwaysFailingAdapter())
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _noop_apply_context_updates)
    return _on_fail


@pytest.mark.asyncio
async def test_same_run_failure_retry_shares_one_failure_identity(monkeypatch) -> None:
    """Attack 2/5: a resume/retry of the same failed run reuses the persisted
    immutable run identity — the retried failure dispatch carries the SAME
    workflow_run_id, so retries collapse to one effective failure authority
    while distinct runs never do."""
    persistence_manager = _StoringPersistenceManager()
    transport = _DummyTransport(persistence_manager)
    on_fail_calls: list[dict] = []
    _failing_env(monkeypatch, transport, on_fail_calls)

    await transport.handle_user_input_from_api(
        chat_id="same-chat", user_id="user-1", workflow_name="TestFlow",
        message="run A", app_id="app-1",
    )
    run_id = persistence_manager.session.get("workflow_run_id")

    # Reconnect/resume retry of the same run (no new identity minted).
    await transport.handle_user_input_from_api(
        chat_id="same-chat", user_id="user-1", workflow_name="TestFlow",
        message=None, app_id="app-1", initial_agent_name_override="resume-agent",
    )

    assert persistence_manager.session.get("workflow_run_id") == run_id
    assert [c.get("workflow_run_id") for c in on_fail_calls] == [run_id, run_id]


@pytest.mark.asyncio
async def test_distinct_workflows_and_apps_have_distinct_failure_identity(monkeypatch) -> None:
    """Attacks 3/4: two workflows in one chat, and two apps reusing a
    chat-like ID, each carry their own run identity and app scope."""
    persistence_manager = _StoringPersistenceManager()
    transport = _DummyTransport(persistence_manager)
    on_fail_calls: list[dict] = []
    _failing_env(monkeypatch, transport, on_fail_calls)

    await transport.handle_user_input_from_api(
        chat_id="same-chat", user_id="user-1", workflow_name="FlowOne",
        message="x", app_id="app-1",
    )
    await transport.handle_user_input_from_api(
        chat_id="same-chat", user_id="user-1", workflow_name="FlowTwo",
        message="x", app_id="app-1",
    )
    await transport.handle_user_input_from_api(
        chat_id="same-chat", user_id="user-1", workflow_name="FlowOne",
        message="x", app_id="app-2",
    )

    run_ids = [c.get("workflow_run_id") for c in on_fail_calls]
    assert len(run_ids) == 3 and len(set(run_ids)) == 3
    assert [c.get("app_id") for c in on_fail_calls] == ["app-1", "app-1", "app-2"]
    assert [c.get("workflow_name") for c in on_fail_calls] == ["FlowOne", "FlowTwo", "FlowOne"]


@pytest.mark.asyncio
async def test_failure_before_any_build_emits_no_build_failed(monkeypatch) -> None:
    """Attacks 6/16: a run failing before any canonical build identity exists
    must not fabricate a build.failed event — the real shared emitter skips
    emission; the typed workflow failure and the run-identified on_fail
    dispatch remain the truthful record."""
    import importlib

    shared = importlib.import_module("factory_app.workflows._shared.platform.build_lifecycle")

    events: list[dict] = []

    async def _session_ctx(**_kw):  # noqa: ANN003
        return {}

    async def _upsert(**kwargs):  # noqa: ANN003
        events.append(dict(kwargs))
        return "outbox_1"

    monkeypatch.setattr(shared, "_get_chat_session_context", _session_ctx)
    monkeypatch.setattr(shared, "upsert_outbox_event", _upsert)
    monkeypatch.setattr(shared, "_materialize_local_app_registry_event", _upsert)
    monkeypatch.setattr(shared, "_spawn_delivery", lambda **_kw: None)

    persistence_manager = _StoringPersistenceManager()
    transport = _DummyTransport(persistence_manager)
    _failing_env(
        monkeypatch, transport, [], hooks={"on_fail": shared.emit_build_failed}
    )

    result = await transport.handle_user_input_from_api(
        chat_id="same-chat", user_id="user-1", workflow_name="AppGenerator",
        message="x", app_id="app-1",
    )
    assert result["status"] == "error"
    assert events == [], "no build.failed may be fabricated for a build that never existed"


@pytest.mark.asyncio
async def test_failure_after_build_exists_binds_run_and_build(monkeypatch) -> None:
    """Attacks 7/17: when the session genuinely carries a canonical build
    identity, build.failed binds to run + build and the idempotency key
    includes both."""
    import importlib

    shared = importlib.import_module("factory_app.workflows._shared.platform.build_lifecycle")

    events: list[dict] = []

    async def _session_ctx(**_kw):  # noqa: ANN003
        return {"build_registry_id": "br-777", "journey_instance_id": "build-777"}

    async def _upsert(**kwargs):  # noqa: ANN003
        events.append(dict(kwargs))
        return "outbox_1"

    async def _noop(**_kw):  # noqa: ANN003
        return None

    monkeypatch.setattr(shared, "_get_chat_session_context", _session_ctx)
    monkeypatch.setattr(shared, "upsert_outbox_event", _upsert)
    monkeypatch.setattr(shared, "_materialize_local_app_registry_event", _noop)
    monkeypatch.setattr(shared, "_spawn_delivery", lambda **_kw: None)

    persistence_manager = _StoringPersistenceManager()
    transport = _DummyTransport(persistence_manager)
    _failing_env(
        monkeypatch, transport, [], hooks={"on_fail": shared.emit_build_failed}
    )

    result = await transport.handle_user_input_from_api(
        chat_id="same-chat", user_id="user-1", workflow_name="AppGenerator",
        message="x", app_id="app-1",
    )
    run_id = persistence_manager.session.get("workflow_run_id")

    assert result["status"] == "error"
    assert len(events) == 1
    assert events[0]["event_type"] == "build.failed"
    assert events[0]["build_id"] == "build-777"
    assert run_id and run_id in events[0]["idempotency_key"]
    assert "build-777" in events[0]["idempotency_key"]
    assert events[0]["payload"]["workflowRunId"] == run_id


@pytest.mark.asyncio
async def test_cancellation_is_not_wrapped_into_run_failure(monkeypatch) -> None:
    """Attack 13: cancellation during the identified run propagates as
    CancelledError — it is never converted into an ordinary run failure and
    dispatches no on_fail."""
    import asyncio as _asyncio

    persistence_manager = _StoringPersistenceManager()
    transport = _DummyTransport(persistence_manager)
    on_fail_calls: list[dict] = []

    class _HangingAdapter:
        async def run(self, request):  # noqa: ANN001
            await _asyncio.sleep(3600)

        async def resume(self, request):  # noqa: ANN001
            await _asyncio.sleep(3600)

    async def _on_fail(**kwargs):  # noqa: ANN003
        on_fail_calls.append(kwargs)

    async def _noop_apply_context_updates(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(
        _bridge_mod, "get_workflow_lifecycle_hooks", lambda _w: {"on_fail": _on_fail}
    )
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: _HangingAdapter())
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _noop_apply_context_updates)

    task = _asyncio.create_task(
        transport.handle_user_input_from_api(
            chat_id="same-chat", user_id="user-1", workflow_name="TestFlow",
            message="x", app_id="app-1",
        )
    )
    await _asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(_asyncio.CancelledError):
        await task
    assert on_fail_calls == []
