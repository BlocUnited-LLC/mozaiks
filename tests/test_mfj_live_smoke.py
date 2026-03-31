from __future__ import annotations

import asyncio
import os

import pytest

from tests.import_utils import import_module_directly


def _live_smoke_enabled() -> bool:
    raw = str(os.getenv("RUN_LIVE_AG2_SMOKE") or "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@pytest.mark.skipif(not _live_smoke_enabled(), reason="Set RUN_LIVE_AG2_SMOKE=1 to run the live AG2 smoke test")
def test_live_mfj_smoke_workflow() -> None:
    module = import_module_directly("scripts.run_live_mfj_smoke")
    result = asyncio.run(
        module.run_live_mfj_smoke(
            prompt="Summarize the smoke workflow in one sentence.",
            timeout_seconds=180.0,
        )
    )

    assert result.success is True
    assert isinstance(result.merged_payload, dict)
    assert isinstance(result.merged_payload.get("result"), str)
    assert result.worker_name == "SmokeChild"
    assert isinstance(result.summary, str) and result.summary
