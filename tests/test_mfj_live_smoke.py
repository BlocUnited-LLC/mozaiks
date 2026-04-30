from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from tests.import_utils import import_module_directly


def _live_smoke_enabled() -> bool:
    raw = str(os.getenv("RUN_LIVE_AG2_SMOKE") or "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@pytest.mark.skipif(not _live_smoke_enabled(), reason="Set RUN_LIVE_AG2_SMOKE=1 to run the live AG2 smoke test")
def test_live_mfj_smoke_workflow() -> None:
    module = import_module_directly("scripts.run_live_mfj_smoke")
    repo_root = Path(__file__).resolve().parents[1]
    workflows_root = repo_root / "platform" / "workflows"
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
