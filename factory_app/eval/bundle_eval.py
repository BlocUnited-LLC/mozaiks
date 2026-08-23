"""Run bundle scorers across a corpus, persist the result, diff against a baseline.

This is the Factory's regression suite runner. Existing quality gates are
pass/fail per build; this makes a Factory change *measurable*: score a fixed
corpus before and after, and block the merge if generated apps got worse. The
CI gate lives in tests/test_factory_regression_suite.py, which materializes
the archetype-matrix corpus offline and diffs against a committed baseline.

Persistence mirrors AG2's evaluation runs — a versioned JSON file per run, joined
on bundle id and scorer key when diffing — so results are comparable across days
and pull requests. The runner is bespoke rather than `ag2.eval.run_agent`
because the subject is a directory of artifacts, not an agent trace.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from .bundle_scorers import score_bundle

SCHEMA_VERSION = "mozaiks.bundle_eval.v1"


@dataclass
class ScorerAggregate:
    key: str
    passed: int = 0
    failed: int = 0
    errored: int = 0
    values: list[float] = field(default_factory=list)

    @property
    def graded(self) -> int:
        return self.passed + self.failed

    @property
    def pass_rate(self) -> float | None:
        return (self.passed / self.graded) if self.graded else None

    @property
    def mean(self) -> float | None:
        return (sum(self.values) / len(self.values)) if self.values else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "pass_rate": self.pass_rate,
            "mean": self.mean,
            "samples": len(self.values),
        }


@dataclass
class BundleRun:
    run_id: str
    bundles: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    aggregates: dict[str, ScorerAggregate] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "bundle_count": len(self.bundles),
            "aggregates": {k: a.to_dict() for k, a in sorted(self.aggregates.items())},
            "bundles": self.bundles,
        }

    def failures(self) -> list[tuple[str, str, str | None]]:
        """(bundle, scorer, comment) for every graded failure."""
        out = []
        for bundle_id, feedback in sorted(self.bundles.items()):
            for fb in feedback:
                if fb.get("score") == 0.0:
                    out.append((bundle_id, fb["key"], fb.get("comment")))
        return out


def _feedback_to_dict(fb: Any) -> dict[str, Any]:
    return {
        "key": fb.key,
        "score": fb.score,
        "value": fb.value,
        "comment": fb.comment,
        "detail": getattr(fb, "detail", None) or {},
    }


def discover_bundles(root: Path) -> list[Path]:
    """Find generated bundles under a corpus root.

    A bundle is any directory containing app.json. Searched two levels deep to
    match the `generated/<app-name>/<build-uuid>/` layout the Factory writes.
    """
    root = Path(root)
    if (root / "app.json").is_file():
        return [root]
    found = [p.parent for p in sorted(root.glob("*/app.json"))]
    found += [p.parent for p in sorted(root.glob("*/*/app.json"))]
    return found


def run_corpus(
    bundle_paths: list[Path],
    *,
    run_id: str,
    corpus_root: Path | None = None,
) -> BundleRun:
    run = BundleRun(run_id=run_id)
    for path in bundle_paths:
        # Identify by path relative to the corpus so ids are stable across
        # machines and cannot collide when two apps share a build name;
        # diff joins on this.
        if corpus_root is not None:
            bundle_id = Path(path).resolve().relative_to(
                Path(corpus_root).resolve()
            ).as_posix()
        else:
            bundle_id = path.name
        feedback = score_bundle(path)
        run.bundles[bundle_id] = [_feedback_to_dict(fb) for fb in feedback]

        for fb in feedback:
            agg = run.aggregates.setdefault(fb.key, ScorerAggregate(key=fb.key))
            if fb.score is None and fb.value is None:
                agg.errored += 1
            elif fb.score is not None:
                if fb.score >= 0.5:
                    agg.passed += 1
                else:
                    agg.failed += 1
            if isinstance(fb.value, (int, float)) and not isinstance(fb.value, bool):
                agg.values.append(float(fb.value))
    return run


def save_run(run: BundleRun, store_dir: Path) -> Path:
    store_dir = Path(store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / f"{run.run_id}.json"
    path.write_text(json.dumps(run.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_run(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class RunDiff:
    current_run_id: str
    baseline_run_id: str
    pass_rate_deltas: dict[str, float] = field(default_factory=dict)
    mean_deltas: dict[str, float] = field(default_factory=dict)
    regressions: list[dict[str, str]] = field(default_factory=list)
    improvements: list[dict[str, str]] = field(default_factory=list)
    only_in_current: list[str] = field(default_factory=list)
    only_in_baseline: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"{self.current_run_id} vs {self.baseline_run_id}"]
        if self.regressions:
            lines.append(f"  {len(self.regressions)} regression(s):")
            for r in self.regressions:
                lines.append(f"    - {r['bundle']} / {r['scorer']} pass -> fail")
        else:
            lines.append("  no regressions")
        if self.improvements:
            lines.append(f"  {len(self.improvements)} fixed")
        for key, delta in sorted(self.pass_rate_deltas.items()):
            if delta:
                lines.append(f"  pass_rate {key}: {delta:+.3f}")
        for key, delta in sorted(self.mean_deltas.items()):
            if delta:
                lines.append(f"  mean {key}: {delta:+.3f}")
        return "\n".join(lines)


def diff_runs(current: dict[str, Any], baseline: dict[str, Any]) -> RunDiff:
    """Join two runs on (bundle, scorer) and report what moved.

    Only bundles present in both are compared — a corpus that grew between runs
    would otherwise read as mass improvement.
    """
    out = RunDiff(
        current_run_id=str(current.get("run_id", "?")),
        baseline_run_id=str(baseline.get("run_id", "?")),
    )

    cur_bundles = current.get("bundles") or {}
    base_bundles = baseline.get("bundles") or {}
    out.only_in_current = sorted(set(cur_bundles) - set(base_bundles))
    out.only_in_baseline = sorted(set(base_bundles) - set(cur_bundles))

    for bundle_id in sorted(set(cur_bundles) & set(base_bundles)):
        cur = {f["key"]: f for f in cur_bundles[bundle_id]}
        base = {f["key"]: f for f in base_bundles[bundle_id]}
        for key in sorted(set(cur) & set(base)):
            before, after = base[key].get("score"), cur[key].get("score")
            if before is None:
                continue
            if after is None:
                # A scorer that produced a pass/fail score in the baseline but
                # returned none now has errored or lost its input. Gate it like
                # a failure — otherwise a scorer that starts raising keeps CI
                # green while its coverage silently disappears.
                if before >= 0.5:
                    out.regressions.append(
                        {"bundle": bundle_id, "scorer": key,
                         "comment": cur[key].get("comment")
                         or "scorer produced no score (errored)"}
                    )
                continue
            if before >= 0.5 and after < 0.5:
                out.regressions.append(
                    {"bundle": bundle_id, "scorer": key,
                     "comment": cur[key].get("comment") or ""}
                )
            elif before < 0.5 and after >= 0.5:
                out.improvements.append({"bundle": bundle_id, "scorer": key})

    cur_agg = current.get("aggregates") or {}
    base_agg = baseline.get("aggregates") or {}
    for key in sorted(set(cur_agg) & set(base_agg)):
        for field_name, target in (("pass_rate", out.pass_rate_deltas), ("mean", out.mean_deltas)):
            a, b = cur_agg[key].get(field_name), base_agg[key].get(field_name)
            if a is not None and b is not None:
                target[key] = round(a - b, 4)

    return out
