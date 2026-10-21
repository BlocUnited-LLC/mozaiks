"""Ask-mode exchanges source workspace truth from server-side state.

The general-mode exchange must not read the per-connection session registry
for "active workflows": an ask-only connection has no workflow contexts of its
own, and the user's real build session lives on another socket. Instead the
exchange reads the session-router snapshot and the host ``ask_context`` hook.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.transport import general_mode as general_mode_module
from mozaiksai.core.transport.general_mode import GeneralModeMixin


class _FakePersistence:
    def __init__(self) -> None:
        self.appended: list[dict[str, Any]] = []

    async def create_general_chat_session(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        return {"chat_id": f"generalchat-{app_id}-{user_id}-0001", "label": "Chat 1", "sequence": 1}

    async def fetch_general_chat_transcript(self, **kwargs) -> None:  # noqa: ANN003
        return None

    async def append_general_message(self, **kwargs) -> dict[str, Any]:  # noqa: ANN003
        self.appended.append(kwargs)
        return {"event_id": f"general_saved_{len(self.appended)}", **kwargs}


class _CapturingService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_response(self, **kwargs) -> dict[str, Any]:  # noqa: ANN003
        self.calls.append(kwargs)
        return {"content": "grounded answer", "usage": {}}


class _StubTransport(GeneralModeMixin):
    def __init__(self) -> None:
        self.connections: dict[str, dict[str, Any]] = {
            "carrier_1": {"app_id": "app_1", "user_id": "user_1", "ws_id": 42}
        }
        self.persistence = _FakePersistence()
        self.sent_events: list[tuple[dict[str, Any], str]] = []
        self.sent_messages: list[dict[str, Any]] = []

    def _get_or_create_persistence_manager(self) -> _FakePersistence:
        return self.persistence

    async def send_event_to_ui(self, payload: dict[str, Any], chat_id: str) -> None:
        self.sent_events.append((payload, chat_id))

    async def send_chat_message(self, content: str, *, agent_name: str, chat_id: str, metadata=None) -> None:  # noqa: ANN001
        self.sent_messages.append(
            {"content": content, "agent_name": agent_name, "chat_id": chat_id, "metadata": metadata}
        )


@pytest.mark.asyncio
async def test_general_exchange_uses_session_snapshot_and_ask_context_hook(monkeypatch):
    import mozaiksai.core.session as session_module
    from mozaiksai.core.runtime.composition import platform_hooks as hooks_module
    from mozaiksai.core.tokens.manager import TokenManager

    snapshot = {
        "current_workflow_id": "ValueEngine",
        "current_chat_id": "chat_real_build",
        "lifecycle_state": "active",
    }

    class _FakeRouter:
        async def get_session_snapshot(self, *, app_id: str, user_id: str) -> dict[str, Any]:
            assert app_id == "app_1"
            assert user_id == "user_1"
            return dict(snapshot)

    class _FakeHooks:
        async def call_ask_context(
            self,
            *,
            app_id: str,
            user_id: str,
            page_path: str | None = None,
            page_context: str | None = None,
            persistence_principal=None,
            principal=None,
        ) -> dict[str, Any]:
            assert app_id == "app_1"
            assert user_id == "user_1"
            assert page_path == "/apps"
            assert page_context == "Apps page"
            return {"Workspace apps": "8 total — 8 draft"}

    service = _CapturingService()
    monkeypatch.setattr(session_module, "get_session_router", lambda: _FakeRouter())
    monkeypatch.setattr(hooks_module, "get_platform_hooks", lambda: _FakeHooks())
    monkeypatch.setattr(general_mode_module, "_load_general_agent_service", lambda: service)

    async def _no_usage(**kwargs) -> None:  # noqa: ANN003
        return None

    monkeypatch.setattr(TokenManager, "emit_usage_delta", _no_usage)

    transport = _StubTransport()
    await transport._handle_general_agent_exchange(
        chat_id="carrier_1",
        ws_id=42,
        user_message="how many apps do I have?",
        ui_context={"page_context": "Apps page", "page_path": "/apps"},
    )

    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["workflows"] == [
        {"workflow_name": "ValueEngine", "chat_id": "chat_real_build", "status": "active"}
    ]
    assert call["workspace_context"] == {"Workspace apps": "8 total — 8 draft"}
    assert call["ui_context"] == {"page_context": "Apps page", "page_path": "/apps"}
    assert transport.sent_messages and transport.sent_messages[-1]["content"] == "grounded answer"


@pytest.mark.asyncio
async def test_general_exchange_forwards_persisted_identity_for_repeated_messages(monkeypatch):
    """History and live replies share identity without deduplicating equal text."""
    import mozaiksai.core.session as session_module
    from mozaiksai.core.runtime.composition import platform_hooks as hooks_module
    from mozaiksai.core.tokens.manager import TokenManager

    router = SimpleNamespace(get_session_snapshot=AsyncMock(return_value={}))
    monkeypatch.setattr(session_module, "get_session_router", lambda: router)
    hooks = AsyncMock()
    hooks.call_ask_context.return_value = {}
    monkeypatch.setattr(hooks_module, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(general_mode_module, "_load_general_agent_service", _CapturingService)
    monkeypatch.setattr(TokenManager, "emit_usage_delta", AsyncMock())
    transport = _StubTransport()

    for _ in range(2):
        await transport._handle_general_agent_exchange(
            chat_id="carrier_1", ws_id=42, user_message="repeat this", ui_context=None
        )

    assert [event[0]["metadata"]["general_message_id"] for event in transport.sent_events] == [
        "general_saved_1", "general_saved_3"
    ]
    assert [message["metadata"]["general_message_id"] for message in transport.sent_messages] == [
        "general_saved_2", "general_saved_4"
    ]
    assert [message["content"] for message in transport.sent_messages] == [
        "grounded answer", "grounded answer"
    ]

    # An unpersisted System response must not borrow the preceding user's ID.
    monkeypatch.setattr(general_mode_module, "_load_general_agent_service", lambda: None)
    await transport._handle_general_agent_exchange(
        chat_id="carrier_1", ws_id=42, user_message="try again", ui_context=None
    )
    assert transport.sent_events[-1][0]["metadata"]["general_message_id"] == "general_saved_5"
    assert transport.sent_messages[-1]["agent_name"] == "System"
    assert "general_message_id" not in transport.sent_messages[-1]["metadata"]


@pytest.mark.asyncio
async def test_general_exchange_does_not_invent_persisted_identity_on_write_failure(monkeypatch):
    import mozaiksai.core.session as session_module
    from mozaiksai.core.runtime.composition import platform_hooks as hooks_module
    from mozaiksai.core.tokens.manager import TokenManager

    router = SimpleNamespace(get_session_snapshot=AsyncMock(return_value={}))
    monkeypatch.setattr(session_module, "get_session_router", lambda: router)
    hooks = AsyncMock()
    hooks.call_ask_context.return_value = {}
    monkeypatch.setattr(hooks_module, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(general_mode_module, "_load_general_agent_service", _CapturingService)
    monkeypatch.setattr(TokenManager, "emit_usage_delta", AsyncMock())
    transport = _StubTransport()
    monkeypatch.setattr(
        transport.persistence, "append_general_message", AsyncMock(side_effect=RuntimeError("offline"))
    )

    await transport._handle_general_agent_exchange(
        chat_id="carrier_1", ws_id=42, user_message="hello", ui_context=None
    )

    assert "general_message_id" not in transport.sent_events[0][0]["metadata"]
    assert "general_message_id" not in transport.sent_messages[0]["metadata"]
    assert transport.sent_messages[0]["content"] == "grounded answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("finished_state", ["completed", "stale"])
async def test_general_exchange_omits_finished_sessions_from_active_workflows(
    monkeypatch, finished_state
):
    """A finished journey keeps current_workflow_id on the session document.

    Reporting it would have the agent insist a build is running days after it
    ended, so finished lifecycle states must not reach the prompt.
    """
    import mozaiksai.core.session as session_module
    from mozaiksai.core.runtime.composition import platform_hooks as hooks_module
    from mozaiksai.core.tokens.manager import TokenManager

    class _FakeRouter:
        async def get_session_snapshot(self, *, app_id: str, user_id: str) -> dict[str, Any]:
            _ = app_id, user_id
            return {
                "current_workflow_id": "ValueEngine",
                "current_chat_id": "chat_finished",
                "lifecycle_state": finished_state,
            }

    class _EmptyHooks:
        async def call_ask_context(self, **kwargs) -> dict[str, Any]:  # noqa: ANN003
            return {}

    service = _CapturingService()
    monkeypatch.setattr(session_module, "get_session_router", lambda: _FakeRouter())
    monkeypatch.setattr(hooks_module, "get_platform_hooks", lambda: _EmptyHooks())
    monkeypatch.setattr(general_mode_module, "_load_general_agent_service", lambda: service)

    async def _no_usage(**kwargs) -> None:  # noqa: ANN003
        return None

    monkeypatch.setattr(TokenManager, "emit_usage_delta", _no_usage)

    transport = _StubTransport()
    await transport._handle_general_agent_exchange(
        chat_id="carrier_1", ws_id=42, user_message="is my build running?", ui_context=None
    )

    assert service.calls[0]["workflows"] == []


@pytest.mark.asyncio
async def test_general_exchange_survives_snapshot_and_hook_failures(monkeypatch):
    import mozaiksai.core.session as session_module
    from mozaiksai.core.runtime.composition import platform_hooks as hooks_module
    from mozaiksai.core.tokens.manager import TokenManager

    def _broken_router():
        raise RuntimeError("session store offline")

    class _BrokenHooks:
        async def call_ask_context(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("hook registry offline")

    service = _CapturingService()
    monkeypatch.setattr(session_module, "get_session_router", _broken_router)
    monkeypatch.setattr(hooks_module, "get_platform_hooks", lambda: _BrokenHooks())
    monkeypatch.setattr(general_mode_module, "_load_general_agent_service", lambda: service)

    async def _no_usage(**kwargs) -> None:  # noqa: ANN003
        return None

    monkeypatch.setattr(TokenManager, "emit_usage_delta", _no_usage)

    transport = _StubTransport()
    await transport._handle_general_agent_exchange(
        chat_id="carrier_1",
        ws_id=42,
        user_message="hello",
        ui_context=None,
    )

    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["workflows"] == []
    assert call["workspace_context"] is None
    assert transport.sent_messages and transport.sent_messages[-1]["content"] == "grounded answer"
