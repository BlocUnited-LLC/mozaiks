from __future__ import annotations

from datetime import datetime, timezone
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

_rt_mod = importlib.import_module("mozaiksai.core.observability.realtime_token_logger")
RealtimeTokenLogger = _rt_mod.RealtimeTokenLogger


@pytest.mark.asyncio
async def test_realtime_token_logger_mirrors_usage_delta_to_performance_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = RealtimeTokenLogger()
    logger.configure(
        chat_id="chat-1",
        workflow_name="ValueEngine",
        app_id="app-1",
        user_id="user-1",
        delegate=None,
    )
    logger._persistence = SimpleNamespace(update_session_metrics=AsyncMock())

    mirror_usage = AsyncMock()
    emit_usage_delta = AsyncMock()

    monkeypatch.setattr(_rt_mod, "_mirror_usage_delta_to_performance_manager", mirror_usage)
    monkeypatch.setattr(
        _rt_mod,
        "TokenManager",
        SimpleNamespace(emit_usage_delta=emit_usage_delta),
    )

    await logger._record_agent_metrics(
        invocation_id="inv-1",
        agent_name="ValueInterviewAgent",
        prompt_tokens=101,
        completion_tokens=55,
        cost=0.0123,
        duration_sec=1.25,
        model_name="gpt-test",
        event_ts=datetime.now(timezone.utc),
        cached=False,
    )

    logger._persistence.update_session_metrics.assert_awaited_once()
    mirror_usage.assert_awaited_once_with(
        chat_id="chat-1",
        agent_name="ValueInterviewAgent",
        model_name="gpt-test",
        prompt_tokens=101,
        completion_tokens=55,
        cost=0.0123,
        duration_sec=1.25,
    )
    emit_usage_delta.assert_awaited_once()
