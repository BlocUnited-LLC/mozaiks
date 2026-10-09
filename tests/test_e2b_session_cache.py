"""Process-local SDK handles remain bounded without reconnecting sealed E2B sessions."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from itertools import count
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.import_utils import import_module_directly

e2b_sandbox = import_module_directly("mozaiksai.core.adapters.e2b_sandbox")
E2BSandboxAdapter = e2b_sandbox.E2BSandboxAdapter
_EXACT_BUILD = "preview:f47ac10b-58cc-4372-a567-0e02b2c3d479"


class _Sandbox:
    def __init__(self, session_id: str, purpose: str) -> None:
        self.sandbox_id = session_id
        self.details = SimpleNamespace(
            metadata={"purpose": purpose},
            allow_internet_access=purpose != "sealed_candidate_preview",
            network={"allow_public_traffic": purpose != "sealed_candidate_preview"},
            lifecycle={"on_timeout": "kill", "auto_resume": False},
            template_id="template-id",
            end_at=datetime.now(UTC) + timedelta(seconds=60),
        )

    def get_info(self):
        return self.details


@pytest.mark.asyncio
async def test_ordinary_handles_are_bounded_and_evicted_sessions_can_reconnect(monkeypatch):
    monkeypatch.setattr(e2b_sandbox, "_MAX_CACHED_SESSIONS", 2)
    sequence = count(1)
    factory = Mock()
    factory.create.side_effect = lambda **kwargs: _Sandbox(f"ordinary-{next(sequence)}", kwargs["metadata"]["purpose"])
    factory.get_info.return_value = _Sandbox("ordinary-1", "artifact_preview").details
    factory.connect.side_effect = lambda session_id, timeout=None: _Sandbox(session_id, "artifact_preview")
    monkeypatch.setattr(e2b_sandbox, "Sandbox", factory)

    adapter = E2BSandboxAdapter()
    for _ in range(10):
        await adapter.create_session(timeout_seconds=60)
        assert len(adapter._sessions) <= 2
    assert list(adapter._sessions) == ["ordinary-9", "ordinary-10"]

    await adapter.connect(session_id="ordinary-1")
    factory.get_info.assert_called_once_with("ordinary-1")
    factory.connect.assert_called_once()
    assert len(adapter._sessions) == 2


@pytest.mark.asyncio
async def test_cross_worker_kill_stale_sealed_handles_expire_without_auto_resume(monkeypatch):
    monkeypatch.setattr(e2b_sandbox, "_MAX_CACHED_SESSIONS", 2)
    clock = [0.0]
    monkeypatch.setattr(e2b_sandbox, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    sequence = count(1)
    factory = Mock()
    factory.create.side_effect = lambda **kwargs: _Sandbox(f"sealed-{next(sequence)}", kwargs["metadata"]["purpose"])
    factory.get_info.return_value = _Sandbox("sealed-1", "sealed_candidate_preview").details
    factory.kill.return_value = True
    monkeypatch.setattr(e2b_sandbox, "Sandbox", factory)

    original_worker = E2BSandboxAdapter()
    for _ in range(2):
        await original_worker.create_session(
            template=_EXACT_BUILD, timeout_seconds=60, metadata={"purpose": "sealed_candidate_preview"},
        )
    with pytest.raises(ValueError, match="lifetime"):
        await original_worker.connect(session_id="sealed-2", timeout_seconds=900)
    with pytest.raises(ValueError, match="lifetime"):
        await original_worker.extend_session(session_id="sealed-2", timeout_seconds=900)
    with pytest.raises(RuntimeError, match="cache is full"):
        await original_worker.create_session(
            template=_EXACT_BUILD, timeout_seconds=60, metadata={"purpose": "sealed_candidate_preview"},
        )
    assert factory.create.call_count == 2
    factory.get_info.side_effect = ConnectionError("provider unavailable")
    with pytest.raises(RuntimeError, match="cache is full"):
        await original_worker.create_session(
            template=_EXACT_BUILD, timeout_seconds=60, metadata={"purpose": "sealed_candidate_preview"},
        )
    assert factory.create.call_count == 2
    factory.get_info.side_effect = None

    other_worker = E2BSandboxAdapter()
    assert await other_worker.terminate_session(session_id="sealed-1") is True
    assert len(original_worker._sessions) == 2
    monkeypatch.setattr(e2b_sandbox, "_NOT_FOUND_ERRORS", (FileNotFoundError,))

    def get_info(session_id, **_kwargs):
        if session_id == "sealed-1":
            raise FileNotFoundError(session_id)
        return _Sandbox(session_id, "sealed_candidate_preview").details

    factory.get_info.side_effect = get_info
    await original_worker.create_session(
        template=_EXACT_BUILD, timeout_seconds=60, metadata={"purpose": "sealed_candidate_preview"},
    )
    assert factory.create.call_count == 3
    assert "sealed-1" not in original_worker._sessions
    assert len(original_worker._sessions) == 2

    clock[0] = 61.0
    with pytest.raises(RuntimeError, match="cannot reconnect"):
        await original_worker.connect(session_id="sealed-2")
    factory.connect.assert_not_called()
    assert not original_worker._sessions
    await original_worker.create_session(
        template=_EXACT_BUILD, timeout_seconds=60, metadata={"purpose": "sealed_candidate_preview"},
    )
    assert len(original_worker._sessions) == 1


@pytest.mark.asyncio
async def test_in_flight_allocations_count_against_cache_bound(monkeypatch):
    monkeypatch.setattr(e2b_sandbox, "_MAX_CACHED_SESSIONS", 1)
    started, release = threading.Event(), threading.Event()
    factory = Mock()

    def create(**kwargs):
        started.set()
        assert release.wait(timeout=5)
        return _Sandbox("ordinary-1", kwargs["metadata"]["purpose"])

    factory.create.side_effect = create
    monkeypatch.setattr(e2b_sandbox, "Sandbox", factory)
    adapter = E2BSandboxAdapter()
    first = asyncio.create_task(adapter.create_session())
    assert await asyncio.to_thread(started.wait, 5)
    try:
        with pytest.raises(RuntimeError, match="cache is full"):
            await adapter.create_session()
    finally:
        release.set()
    await first
    assert factory.create.call_count == 1
    assert len(adapter._sessions) == 1
