"""Factory regression suite — the scored gate over the archetype corpus.

The archetype matrix test asserts hard invariants per archetype. This suite
adds the *graded* layer the Build Intelligence phasing calls generation-time
evaluation: materialize the same five representative archetypes offline (no
LLM, no network — frozen AppBuildPlans through the production
app_build_plan → task batches → assemble_app_tasks pipeline), score every
bundle with factory_app.eval's deterministic scorers, and diff the run
against a committed baseline. A scorer that passed on the baseline and fails
now blocks the merge — a Factory change that made generated apps worse.

Refreshing the baseline is a deliberate, reviewed act:

    REFRESH_FACTORY_EVAL_BASELINE=1 pytest tests/test_factory_regression_suite.py

then commit the changed fixture and explain the delta in the PR. Value-type
scorers (module_count, action_gate_coverage, ...) are trend signal and do not
gate; only pass→fail transitions do.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from factory_app.eval import diff_runs, run_corpus
from tests.test_generated_app_archetype_matrix import _materialize_spec, _matrix_specs

BASELINE_PATH = Path(__file__).resolve().parent / "fixtures" / "factory_bundle_eval_baseline.json"
REFRESH_ENV = "REFRESH_FACTORY_EVAL_BASELINE"
RUN_ID = "factory-regression"


async def _score_corpus(tmp_path: Path):
    specs = _matrix_specs()
    assert len(specs) >= 5, "archetype corpus unexpectedly shrank"
    bundle_paths: list[Path] = []
    for spec in specs:
        _files, app_root, _loaded = await _materialize_spec(spec, tmp_path)
        bundle_paths.append(app_root)
    return run_corpus(bundle_paths, run_id=RUN_ID, corpus_root=tmp_path)


@pytest.mark.asyncio
async def test_factory_regression_suite_matches_baseline(tmp_path: Path) -> None:
    run = await _score_corpus(tmp_path)

    if os.environ.get(REFRESH_ENV):
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps(run.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip(f"baseline refreshed at {BASELINE_PATH}; review and commit it")

    assert BASELINE_PATH.is_file(), (
        f"No committed baseline at {BASELINE_PATH}. Generate one with "
        f"{REFRESH_ENV}=1 and commit it."
    )
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    delta = diff_runs(run.to_dict(), baseline)
    assert not delta.only_in_baseline, (
        "Bundles present in the baseline are missing from this run "
        f"(corpus shrank?): {delta.only_in_baseline}"
    )
    assert not delta.regressions, (
        "FACTORY_REGRESSION — generated apps got worse on a previously "
        "passing check:\n" + delta.summary()
    )


@pytest.mark.asyncio
async def test_factory_regression_run_is_deterministic(tmp_path: Path) -> None:
    """Two materialize+score passes must agree exactly.

    If this fails, the suite is measuring noise, and baseline diffs are
    meaningless — fix determinism before trusting any regression signal.
    """
    first = await _score_corpus(tmp_path / "a")
    second = await _score_corpus(tmp_path / "b")
    assert first.to_dict() == second.to_dict()
