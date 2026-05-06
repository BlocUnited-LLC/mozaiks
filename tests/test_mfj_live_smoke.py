from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from tests.import_utils import import_module_directly


def _live_smoke_enabled() -> bool:
    raw = str(os.getenv("RUN_LIVE_AG2_SMOKE") or "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _live_agentgenerator_smoke_enabled() -> bool:
    raw = str(os.getenv("RUN_LIVE_AGENTGENERATOR_SMOKE") or "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@pytest.mark.skipif(not _live_smoke_enabled(), reason="Set RUN_LIVE_AG2_SMOKE=1 to run the live AG2 smoke test")
def test_live_mfj_smoke_workflow() -> None:
    module = import_module_directly("scripts.run_live_mfj_smoke")
    repo_root = Path(__file__).resolve().parents[1]
    workflows_root = repo_root / "factory_app" / "workflows"
    result = asyncio.run(
        module.run_live_mfj_smoke(
            workflow_name="RuntimeSmoke",
            workflows_root=workflows_root,
            prompt="Confirm runtime orchestration in one sentence.",
            timeout_seconds=180.0,
        )
    )

    assert result.success is True
    assert result.workflow_name == "RuntimeSmoke"
    assert isinstance(result.structured_output, dict)
    assert isinstance(result.structured_output.get("result"), str)
    assert isinstance(result.assistant_message, str) and result.assistant_message


@pytest.mark.skipif(
    not _live_agentgenerator_smoke_enabled(),
    reason="Set RUN_LIVE_AGENTGENERATOR_SMOKE=1 to run the live AgentGenerator smoke test",
)
def test_live_agentgenerator_smoke_workflow() -> None:
    module = import_module_directly("scripts.run_live_mfj_smoke")
    repo_root = Path(__file__).resolve().parents[1]
    workflows_root = repo_root / "factory_app" / "workflows"
    prompt = module._load_prompt_file(workflows_root / "AgentGenerator" / "smoke_prompt.txt")
    scripted = module._load_tool_response_file(workflows_root / "AgentGenerator" / "smoke_responses.json")

    result = asyncio.run(
        module.run_live_mfj_smoke(
            workflow_name="AgentGenerator",
            workflows_root=workflows_root,
            prompt=prompt,
            timeout_seconds=300.0,
            user_replies=list(scripted.get("input_replies") or []),
            tool_response_payloads=scripted.get("tool_responses"),
            default_input_reply=scripted.get("default_input_reply"),
            assistant_reply_rules=scripted.get("assistant_reply_rules"),
        )
    )

    assert result.success is True
    assert result.workflow_name == "AgentGenerator"
    assert isinstance(result.structured_output, dict)
    assert result.structured_output
