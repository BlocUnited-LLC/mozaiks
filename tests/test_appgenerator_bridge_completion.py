"""Production-bridge completion traversal for AppGenerator lifecycle claims.

Exercises the REAL callsite chain: workflow_bridge._launch_workflow_run_locked
→ persisted final-context fetch → the real AppGenerator on_complete hook
resolved through get_workflow_lifecycle_hooks → the real shared lifecycle
emitters, intercepted at the outbox boundary. The hook is never invoked
directly by these tests.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.ports.orchestration import RunResult, RunStatus
from mozaiksai.core.runtime.composition.extensions import get_workflow_lifecycle_hooks
from mozaiksai.core.transport.workflow_bridge import WorkflowBridgeMixin

_shared_lifecycle = __import__(
    "factory_app.workflows._shared.platform.build_lifecycle",
    fromlist=["build_lifecycle"],
)


class _FakePersistenceManager:
    def __init__(self, session_context: dict[str, Any] | None) -> None:
        self._session_context = session_context

    async def fetch_chat_session_extra_context(self, **_kwargs: Any) -> dict[str, Any]:
        if self._session_context is None:
            raise RuntimeError("session context unavailable")
        return dict(self._session_context)


class _Bridge(WorkflowBridgeMixin):
    def __init__(self, session_context: dict[str, Any] | None) -> None:
        self._pm = _FakePersistenceManager(session_context)

    def _get_or_create_persistence_manager(self) -> _FakePersistenceManager:
        return self._pm

    async def _emit_synthetic_run_complete_if_needed(self, **_kwargs: Any) -> None:
        return None


class _FakeAdapter:
    def __init__(self, status: RunStatus) -> None:
        self._status = status

    async def run(self, request: Any) -> RunResult:
        return RunResult(
            status=self._status,
            chat_id=request.chat_id,
            workflow_name=request.workflow_name,
        )

    async def resume(self, request: Any) -> RunResult:  # pragma: no cover
        return RunResult(
            status=self._status,
            chat_id=request.chat_id,
            workflow_name=request.workflow_name,
        )


def _intercept_lifecycle(monkeypatch) -> tuple[list[dict[str, Any]], asyncio.Event]:
    """Route the real shared emitters' persistence boundary into memory."""
    events: list[dict[str, Any]] = []
    fired = asyncio.Event()

    async def _session_context(**_kwargs: Any) -> dict[str, Any]:
        return {}

    async def _upsert(**kwargs: Any) -> str:
        events.append({"event_type": kwargs.get("event_type"), "status": kwargs.get("status")})
        fired.set()
        return f"outbox_{len(events)}"

    async def _artifacts(**_kwargs: Any) -> dict[str, Any]:
        return {}

    async def _materialize(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(_shared_lifecycle, "_get_chat_session_context", _session_context)
    monkeypatch.setattr(_shared_lifecycle, "upsert_outbox_event", _upsert)
    monkeypatch.setattr(_shared_lifecycle, "get_build_artifacts", _artifacts)
    monkeypatch.setattr(_shared_lifecycle, "_materialize_local_app_registry_event", _materialize)
    monkeypatch.setattr(_shared_lifecycle, "_spawn_delivery", lambda **_kw: None)

    import mozaiksai.core.artifacts.summary_artifacts as summary_artifacts

    monkeypatch.setattr(summary_artifacts, "persist_summary_artifact", AsyncMock())
    return events, fired


async def _run_bridge(
    monkeypatch,
    *,
    session_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    hooks = get_workflow_lifecycle_hooks("AppGenerator")
    assert hooks["on_complete"] is not None, "AppGenerator on_complete hook must resolve"

    events, fired = _intercept_lifecycle(monkeypatch)

    import mozaiksai.core.adapters.ag2_orchestration as orchestration

    monkeypatch.setattr(
        orchestration, "get_ag2_adapter", lambda: _FakeAdapter(RunStatus.COMPLETED)
    )

    bridge = _Bridge(session_context)
    result = await bridge._launch_workflow_run_locked(
        chat_id="chat-bridge",
        user_id="user-1",
        workflow_name="AppGenerator",
        message=None,
        app_id="app-bridge",
        initial_agent_name_override=None,
        is_resume_request=False,
        emit_execution_started=None,
        emit_execution_completed=hooks["on_complete"],
    )
    assert result["run_status"] == "completed"
    # The completion hook runs as a task; give it a bounded window.
    try:
        await asyncio.wait_for(fired.wait(), timeout=5)
    except TimeoutError:
        pass
    await asyncio.sleep(0)
    return events


@pytest.mark.asyncio
async def test_successful_production_run_emits_exactly_one_completed(monkeypatch) -> None:
    """The reviewer's attack: a successful run must not be classified failed."""
    events = await _run_bridge(
        monkeypatch,
        session_context={"download_status": "ready", "app_download_ready": True},
    )
    completed = [e for e in events if e["event_type"] == "build.completed"]
    failed = [e for e in events if e["event_type"] == "build.failed"]
    assert len(completed) == 1, events
    assert failed == []


@pytest.mark.asyncio
async def test_failed_terminal_tool_emits_exactly_one_failed(monkeypatch) -> None:
    events = await _run_bridge(
        monkeypatch,
        session_context={"download_status": "failed", "app_download_ready": False},
    )
    completed = [e for e in events if e["event_type"] == "build.completed"]
    failed = [e for e in events if e["event_type"] == "build.failed"]
    assert completed == []
    assert len(failed) == 1, events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_context",
    [
        {},
        {"download_status": "cancelled", "app_download_ready": False},
        None,
    ],
    ids=["intermediate-turn", "cancelled-download", "context-fetch-failed"],
)
async def test_nonterminal_runs_claim_nothing(monkeypatch, session_context) -> None:
    events = await _run_bridge(monkeypatch, session_context=session_context)
    assert events == [], events


class TestLifecycleHookComposition:
    """Multiple lifecycle_tools on one trigger must all run, in order.

    AppGenerator declares four on_complete tools; single-slot resolution
    silently dropped emit_build_completed (only the last-declared recorder
    ran), so no production run ever claimed build.completed.
    """

    def test_appgenerator_on_complete_resolves_all_declared_hooks(self) -> None:
        hooks = get_workflow_lifecycle_hooks("AppGenerator")
        on_complete = hooks["on_complete"]
        assert on_complete is not None
        # Multi-hook triggers resolve to the runtime's composed invoker, not
        # to whichever single tool happened to be declared last.
        assert on_complete.__module__ == "mozaiksai.core.runtime.composition.extensions"

    @pytest.mark.asyncio
    async def test_composed_hooks_run_in_order_and_isolate_failures(self) -> None:
        from mozaiksai.core.runtime.composition.extensions import _compose_lifecycle_hooks

        calls: list[str] = []

        async def _first(**_kw: Any) -> str:
            calls.append("first")
            return "outbox_1"

        async def _raising(**_kw: Any) -> None:
            calls.append("raising")
            raise RuntimeError("recorder backend unavailable")

        async def _last(**_kw: Any) -> str:
            calls.append("last")
            return "ignored"

        composed = _compose_lifecycle_hooks([_first, _raising, _last], "on_complete", "AppGenerator")
        result = await composed(app_id="app-1")

        assert calls == ["first", "raising", "last"]
        assert result == "outbox_1"

    @pytest.mark.asyncio
    async def test_composed_hook_failure_does_not_suppress_later_hooks(self) -> None:
        from mozaiksai.core.runtime.composition.extensions import _compose_lifecycle_hooks

        calls: list[str] = []

        async def _raising(**_kw: Any) -> None:
            raise RuntimeError("boom")

        async def _emit(**_kw: Any) -> str:
            calls.append("emit")
            return "outbox_emit"

        composed = _compose_lifecycle_hooks([_raising, _emit], "on_complete", "AppGenerator")
        result = await composed(app_id="app-1")

        assert calls == ["emit"]
        assert result == "outbox_emit"
