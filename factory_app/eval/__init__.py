"""Factory generation-time evaluation: bundle scorers, runs, and diffs.

This package is the OSS source of truth for scoring generated app bundles.
It originated in the hosted product's build_intelligence module (mozaiks-app
PR #229) and moved here because the Factory's regression suite gates factory
changes, which land in this repository.

Deliberate design note (do not "fix" this into ag2.eval.run_agent): the
subject of these scorers is a directory of artifacts, not an agent trace, so
the runner is bespoke. Scorers still return ``ag2.eval.Feedback`` so they
migrate cleanly if the Factory is later evaluated through AG2 runs, and the
persistence/diff shapes mirror AG2's evaluation runs.
"""
from .bundle_eval import (
    BundleRun,
    RunDiff,
    diff_runs,
    discover_bundles,
    load_run,
    run_corpus,
    save_run,
)
from .bundle_scorers import Bundle, all_scorers, score_bundle

__all__ = [
    "Bundle",
    "BundleRun",
    "RunDiff",
    "all_scorers",
    "diff_runs",
    "discover_bundles",
    "load_run",
    "run_corpus",
    "save_run",
    "score_bundle",
]
