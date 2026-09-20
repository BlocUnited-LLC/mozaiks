"""Failed tasks drain their dependents while preserving accepted independent work."""


from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BATCHES = ROOT / "factory_app/workflows/AppGenerator/extended_orchestration/task_batches.yaml"
GRAPH = ROOT / "factory_app/workflows/AppGenerator/transition_graph.yaml"


def _batch() -> dict:
    spec = yaml.safe_load(BATCHES.read_text(encoding="utf-8"))
    return next(b for b in spec["batches"] if b["id"] == "app_build_tasks")


def _rules() -> list[dict]:
    return yaml.safe_load(GRAPH.read_text(encoding="utf-8"))["transition_rules"]


def test_a_failed_task_no_longer_discards_the_batch() -> None:
    assert _batch()["execution"]["failure_policy"] == "continue_with_available"


def test_a_partial_batch_still_reaches_assembly() -> None:
    """Without this the crash becomes a silent stall, which is worse."""
    targets = {
        rule["condition_value"]: rule["target_agent"]
        for rule in _rules()
        if rule.get("source_agent") == "AppPlanAgent"
        and rule.get("condition_key") == "app_task_batch_status"
    }

    assert targets.get("completed") == "AssemblyAgent"
    assert targets.get("partial") == "AssemblyAgent"


@pytest.mark.asyncio
async def test_identical_rejection_stops_and_preserves_independent_work(monkeypatch) -> None:
    from collections import Counter

    from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
    from mozaiksai.core.ports.orchestration import RunStatus
    from mozaiksai.core.workflow import task_batches as tb
    from tests.test_task_batch_recovery import _config, _context, _execute

    config, context, counts = _config(), _context(), Counter()
    config.batches[0].recovery = None

    async def run(self, request):
        counts[request.task_id] += 1
        task = request.context_variables["current_task"]
        files = [{"filename": path, "content": "{}"} for path in task["owned_paths"]]
        if request.task_id == "services":
            files.append({"filename": "modules/task_management/backend/schemas.py", "content": "invalid"})
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output={"code_files": files})

    monkeypatch.setattr(tb.AG2TaskBatchRunner, "run", run)
    await _execute(context, config, [])

    assert counts == {"models": 1, "services": 2, "independent": 1}
    assert context["status"] == "partial"
    assert context["results"]["_failed"]["page"]["blocked_by"] == ["services"]
    assert "models" in context["results"] and "independent" in context["results"]


def test_the_retry_budget_is_unchanged_for_failures_that_differ() -> None:
    """Stopping on repetition must not reduce genuine retry attempts."""
    assert _batch()["execution"]["retry_limit"] == 3
