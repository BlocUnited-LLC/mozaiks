from __future__ import annotations

from types import SimpleNamespace

import pytest
from ag2.events import ModelResponse
from ag2.events.types import Usage

from mozaiksai.core.usage import watchdog as watchdog_mod


class _ContextBridge:
    data = {
        "app_id": "app-1",
        "chat_id": "chat-1",
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "workflow_name": "AppGenerator",
        "token_watchdog_warn_tokens": 10,
        "token_watchdog_alert_tokens": 20,
    }

    def get(self, key, default=None):
        return self.data.get(key, default)


def test_resolve_token_watchdog_thresholds_prefers_context() -> None:
    warn, alert = watchdog_mod.resolve_token_watchdog_thresholds(_ContextBridge())

    assert warn == 10
    assert alert == 20


@pytest.mark.asyncio
async def test_ag2_token_watchdog_bridge_emits_mozaiks_budget_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted: list[dict] = []

    async def fake_emit_token_budget_alert(**payload):
        emitted.append(payload)

    monkeypatch.setattr(watchdog_mod, "emit_token_budget_alert", fake_emit_token_budget_alert)

    token_monitor, bridge = watchdog_mod.build_ag2_token_watchdog_observers(
        agent_name="PlannerAgent",
        workflow_name="AppGenerator",
        context_variables=_ContextBridge(),
    )

    alert = await token_monitor.process(
        [ModelResponse(usage=Usage(prompt_tokens=12, completion_tokens=10, total_tokens=22))],
        SimpleNamespace(),
    )
    assert alert is not None

    await bridge.process([alert], SimpleNamespace())

    assert emitted == [
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
            "user_id": "user-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "workflow_name": "AppGenerator",
            "agent_name": "PlannerAgent",
            "observer_source": "mozaiks-token-monitor",
            "severity": "critical",
            "message": (
                "Token usage critical: 22 tokens (threshold: 20). "
                "Consider wrapping up to control costs."
            ),
            "total_tokens": 22,
            "warn_threshold": 10,
            "alert_threshold": 20,
        }
    ]
