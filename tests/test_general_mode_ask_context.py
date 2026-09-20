"""Ask-mode exchanges source workspace truth from server-side state.

The general-mode exchange must not read the per-connection session registry
for "active workflows": an ask-only connection has no workflow contexts of its
own, and the user's real build session lives on another socket. Instead the
exchange reads the session-router snapshot and the host ``ask_context`` hook.
"""

from __future__ import annotations

from typing import Any

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

    async def append_general_message(self, **kwargs) -> None:  # noqa: ANN003
        self.appended.append(kwargs)


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
