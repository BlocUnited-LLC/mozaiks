"""Provider-neutral readiness evidence evaluation.

This module intentionally works with names only: environment variable names,
evidence stamp names, and canonical paths. It never returns secret values.
Hosted products can layer provider-specific checks on top of this primitive
without copying product policy into the OSS runtime.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

EnvReader = Callable[[str], str | None]
EnvValidator = Callable[[str | None], bool]


@dataclass(frozen=True)
class ReadinessCheck:
    """Names-only readiness requirement for one deploy/runtime concern."""

    id: str
    category: str
    label: str
    implemented_score: int = 0
    required_env: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    canonical_paths: tuple[str, ...] = ()
    notes: str = ""


def os_env(name: str) -> str | None:
    return os.environ.get(name)


def env_present(value: str | None) -> bool:
    return value is not None and str(value).strip() != ""


def truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def non_false_env(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and text not in {"0", "false", "no", "off"}


def _dedupe(names: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = str(raw_name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _name_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return _dedupe(value)


def _int_or_default(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _missing(
    names: Iterable[str],
    *,
    env: EnvReader,
    validators: Mapping[str, EnvValidator],
) -> list[str]:
    missing: list[str] = []
    for name in _dedupe(names):
        validator = validators.get(name, env_present)
        if not validator(env(name)):
            missing.append(name)
    return missing


def readiness_score(
    implemented_score: int,
    *,
    missing_required_env: Sequence[str],
    missing_required_evidence: Sequence[str],
) -> int:
    """Return a conservative 0-10 score for one readiness check."""

    baseline = max(0, min(int(implemented_score), 10))
    if not missing_required_env and not missing_required_evidence:
        return 10
    if missing_required_env:
        return min(baseline, 6)
    return baseline


def _check_row(
    check: ReadinessCheck,
    *,
    env: EnvReader,
    validators: Mapping[str, EnvValidator],
) -> dict[str, Any]:
    missing_env = _missing(check.required_env, env=env, validators=validators)
    missing_evidence = _missing(check.required_evidence, env=env, validators=validators)
    score = readiness_score(
        check.implemented_score,
        missing_required_env=missing_env,
        missing_required_evidence=missing_evidence,
    )
    return {
        "id": check.id,
        "category": check.category,
        "label": check.label,
        "score": score,
        "ready": score == 10,
        "missing_required_env": missing_env,
        "missing_required_evidence": missing_evidence,
        "canonical_paths": list(check.canonical_paths),
        "notes": check.notes,
    }


def summarize_readiness_categories(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Summarize readiness rows by first-seen category order."""

    categories: list[str] = []
    for row in rows:
        category = str(row.get("category") or "").strip()
        if category and category not in categories:
            categories.append(category)

    result: dict[str, dict[str, Any]] = {}
    for category in categories:
        category_rows = [row for row in rows if row.get("category") == category]
        if not category_rows:
            continue
        scores = [int(row.get("score") or 0) for row in category_rows]
        ready_count = sum(1 for row in category_rows if bool(row.get("ready")))
        result[category] = {
            "score": round(sum(scores) / len(scores), 1),
            "ready_count": ready_count,
            "check_count": len(category_rows),
            "ready": ready_count == len(category_rows),
        }
    return result


def evaluate_readiness_checks(
    checks: Sequence[ReadinessCheck],
    *,
    env: EnvReader = os_env,
    validators: Mapping[str, EnvValidator] | None = None,
) -> dict[str, Any]:
    """Evaluate readiness checks without exposing underlying values."""

    safe_validators = validators or {}
    rows = [_check_row(check, env=env, validators=safe_validators) for check in checks]
    categories = summarize_readiness_categories(rows)
    category_scores = [float(item["score"]) for item in categories.values()]
    blocking_checks = [str(row["id"]) for row in rows if not bool(row["ready"])]
    return {
        "ready": not blocking_checks,
        "overall_score": round(sum(category_scores) / len(category_scores), 1) if category_scores else 0.0,
        "blocking_checks": blocking_checks,
        "categories": categories,
        "checks": rows,
    }


def checks_from_readiness_requirements(requirements: Mapping[str, Any] | None) -> tuple[ReadinessCheck, ...]:
    """Build ``ReadinessCheck`` objects from a names-only manifest section."""

    if not isinstance(requirements, Mapping):
        return ()
    raw_checks = requirements.get("checks")
    if not isinstance(raw_checks, list):
        return ()

    checks: list[ReadinessCheck] = []
    for item in raw_checks:
        if not isinstance(item, Mapping):
            continue
        check_id = str(item.get("id") or "").strip()
        category = str(item.get("category") or "").strip()
        label = str(item.get("label") or "").strip()
        if not check_id or not category or not label:
            continue
        checks.append(
            ReadinessCheck(
                id=check_id,
                category=category,
                label=label,
                implemented_score=_int_or_default(item.get("implemented_score"), 0),
                required_env=tuple(_name_list(item.get("required_env"))),
                required_evidence=tuple(_name_list(item.get("required_evidence"))),
                canonical_paths=tuple(_name_list(item.get("canonical_paths"))),
                notes=str(item.get("notes") or "").strip(),
            )
        )
    return tuple(checks)


def evaluate_readiness_requirements(
    requirements: Mapping[str, Any] | None,
    *,
    env: EnvReader = os_env,
    validators: Mapping[str, EnvValidator] | None = None,
) -> dict[str, Any]:
    return evaluate_readiness_checks(
        checks_from_readiness_requirements(requirements),
        env=env,
        validators=validators,
    )


__all__ = [
    "EnvReader",
    "EnvValidator",
    "ReadinessCheck",
    "checks_from_readiness_requirements",
    "env_present",
    "evaluate_readiness_checks",
    "evaluate_readiness_requirements",
    "non_false_env",
    "os_env",
    "readiness_score",
    "summarize_readiness_categories",
    "truthy_env",
]
