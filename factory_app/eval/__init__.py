"""Factory generation-time evaluation: bundle scorers, runs, and diffs.

This package is the supported, experimental Python facade for the reference
Factory's deterministic bundle evaluation. Operators import this package rather
than copying its implementation. Private corpora, tuned scoring policy and
results remain operator-owned; this facade supplies the public reference suite.
It originated in the hosted product's build_intelligence module (mozaiks-app
PR #229) and moved here because the Factory's regression suite gates factory
changes, which land in this repository.

Deliberate design note (do not "fix" this into ag2.eval.run_agent): the
subject of these scorers is a directory of artifacts, not an agent trace, so
the runner is bespoke. Scorers return a local, AG2-inspired ``Feedback``
dataclass rather than ``ag2.eval.Feedback``: it deliberately types ``value``
as ``Any`` so numeric scorers can feed distribution aggregation, diverging
from ag2 1.0.1's categorical ``value: str | None``. The persistence/diff
shapes mirror AG2's evaluation runs.
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
from .bundle_scorers import Bundle, Feedback, all_scorers, score_bundle
from .evidence import GenerationEvidence, collect_generation_evidence

__all__ = [
    "Bundle",
    "BundleRun",
    "Feedback",
    "GenerationEvidence",
    "RunDiff",
    "all_scorers",
    "collect_generation_evidence",
    "diff_runs",
    "discover_bundles",
    "load_run",
    "run_corpus",
    "save_run",
    "score_bundle",
]
