"""Runtime quality gate for generated module backend code.

This gate runs after ServiceAgent and before frontend/controller assembly.  The
module contract gate validates YAML shape; this module validates that generated
Python does not ship fake runtime facts such as hardcoded KPI counts,
placeholder trends, demo/sample records, or TODO-backed business logic.
"""

from __future__ import annotations

import ast
import logging
import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Annotated, Any

from autogen.tools.dependency_injection import Field

logger = logging.getLogger(__name__)

_RUNTIME_BACKEND_FILE = re.compile(r"(^|/)modules/[^/]+/backend/[^/]+\.py$")
_SUMMARY_FUNCTION = re.compile(r"(summary|stats|metrics|metric|count|dashboard|overview)", re.I)
_TREND_KEY = re.compile(r"(trend|change|growth|delta|rate)", re.I)
_PERCENT_TEXT = re.compile(r"[+-]?\d+(?:\.\d+)?\s*%")

_BANNED_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmock(?:ed|ing)?\b", re.I), "mock runtime data"),
    (re.compile(r"\bfake\b", re.I), "fake runtime data"),
    (re.compile(r"\bsample(?:s)?\b", re.I), "sample runtime data"),
    (re.compile(r"\bplaceholder\b", re.I), "placeholder runtime data"),
    (re.compile(r"\bdummy\b", re.I), "dummy runtime data"),
    (re.compile(r"\bdemo\b", re.I), "demo runtime data"),
    (re.compile(r"\blorem\b", re.I), "lorem placeholder text"),
    (re.compile(r"\bTODO\b", re.I), "TODO runtime logic"),
    (re.compile(r"\bNotImplemented(?:Error)?\b", re.I), "unimplemented runtime logic"),
    (re.compile(r"\bexample\b", re.I), "example runtime logic"),
    (re.compile(r"\bimplement(?:\s+(?:additional|the|own))?\s+(?:logic|checks|authorization|persistence)", re.I), "unfinished runtime logic"),
    (re.compile(r"\bin production\b", re.I), "production-only placeholder branch"),
    (re.compile(r"\brandom\.", re.I), "randomized runtime facts"),
    (re.compile(r"\bfaker\b", re.I), "faker-generated runtime facts"),
)

_SUSPICIOUS_NAME = re.compile(
    r"(SAMPLE|MOCK|FAKE|PLACEHOLDER|DEMO|DUMMY|FALLBACK_(?:SUMMARY|STATS|METRICS)|_fallback_(?:summary|stats|metrics))"
)

_DATA_ACCESS_NAMES = {
    "repo",
    "db",
    "database",
    "collection",
    "collections",
}

_DATA_ACCESS_ATTRS = {
    "repo",
    "db",
    "database",
    "collection",
    "count_documents",
    "find",
    "find_one",
    "aggregate",
    "distinct",
    "estimated_document_count",
}


def _context_get(context_variables: Any | None, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            value = context_variables.get(key)
            return default if value is None else value
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _context_set(context_variables: Any | None, key: str, value: Any) -> None:
    if context_variables is None:
        return
    if hasattr(context_variables, "set"):
        try:
            context_variables.set(key, value)
            return
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        data[key] = value
        return
    if isinstance(context_variables, dict):
        context_variables[key] = value


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dedupe(warnings: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for warning in warnings:
        if warning not in seen:
            seen.add(warning)
            out.append(warning)
    return out


def _safe_path(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    normalized = raw.replace("\\", "/").strip()
    try:
        path = PurePosixPath(normalized)
    except Exception:
        return ""
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return ""
    return str(path)


def _iter_backend_python_files(code_files: Any) -> Iterable[tuple[str, str]]:
    if not isinstance(code_files, list):
        return
    for item in code_files:
        if not isinstance(item, dict):
            continue
        filename = _safe_path(item.get("filename") or item.get("path"))
        content = item.get("content")
        if not filename or content is None:
            continue
        if _RUNTIME_BACKEND_FILE.search(filename):
            yield filename, str(content)


def _is_summary_function(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and bool(
        _SUMMARY_FUNCTION.search(node.name)
    )


def _has_data_dependency(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in _DATA_ACCESS_NAMES:
            return True
        if isinstance(child, ast.Attribute) and child.attr in _DATA_ACCESS_ATTRS:
            return True
    return False


def _literal_metric_value(node: ast.AST, *, trend_context: bool = False) -> bool:
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None or isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            lowered = value.lower()
            return bool(
                trend_context
                or _PERCENT_TEXT.search(value)
                or any(token in lowered for token in ("change", "growth", "trend", "demo", "sample"))
            )
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_literal_metric_value(item, trend_context=trend_context) for item in node.elts)
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            key_text = key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else ""
            if _literal_metric_value(value, trend_context=trend_context or bool(_TREND_KEY.search(key_text))):
                return True
    return False


def _static_trend_value(node: ast.AST) -> bool:
    if not isinstance(node, ast.Dict):
        return False
    for key, value in zip(node.keys, node.values):
        key_text = key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else ""
        if not _TREND_KEY.search(key_text):
            continue
        if isinstance(value, ast.Constant) and value.value is None:
            continue
        if _literal_metric_value(value, trend_context=True):
            return True
    return False


def _audit_ast(filename: str, content: str) -> list[str]:
    warnings: list[str] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Syntax is handled by build validation. Avoid duplicating that concern here.
        return warnings

    for node in ast.walk(tree):
        if isinstance(node, ast.Pass):
            warnings.append(
                f"{filename}:{node.lineno}: runtime function contains pass; generated module runtime code must execute real logic or return an honest value."
            )

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _SUSPICIOUS_NAME.search(node.name):
                warnings.append(
                    f"{filename}:{node.lineno}: runtime placeholder function '{node.name}' is not allowed."
                )

        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets: list[ast.AST]
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                targets = [node.target]
            for target in targets:
                name = target.id if isinstance(target, ast.Name) else ""
                if name and _SUSPICIOUS_NAME.search(name):
                    warnings.append(
                        f"{filename}:{node.lineno}: runtime placeholder variable '{name}' is not allowed."
                    )

        if not _is_summary_function(node):
            continue

        has_data = _has_data_dependency(node)
        for child in ast.walk(node):
            if not isinstance(child, ast.Return) or child.value is None:
                continue
            if _static_trend_value(child.value):
                warnings.append(
                    f"{filename}:{child.lineno}: summary/stat function '{node.name}' returns a static trend/change value."
                )
            if not has_data and _literal_metric_value(child.value):
                warnings.append(
                    f"{filename}:{child.lineno}: summary/stat function '{node.name}' returns static metrics without repo/db-backed data access."
                )
                break
    return warnings


def audit_module_runtime_quality(code_files: list[dict[str, Any]]) -> list[str]:
    """Return deterministic warnings for generated module backend code."""

    warnings: list[str] = []
    for filename, content in _iter_backend_python_files(code_files):
        for pattern, label in _BANNED_TEXT_PATTERNS:
            match = pattern.search(content)
            if match:
                warnings.append(
                    f"{filename}: contains {label} ({match.group(0)!r}); generated module runtime code must use repo-backed data or honest empty states."
                )
        warnings.extend(_audit_ast(filename, content))
    return _dedupe(warnings)


def count_module_backend_files(code_files: Any) -> int:
    return sum(1 for _filename, _content in _iter_backend_python_files(code_files))


def review_module_runtime_quality(
    max_revision_attempts: Annotated[
        int,
        Field(
            description=(
                "Maximum number of ServiceAgent revision loops allowed before "
                "blocking for user/operator review."
            )
        ),
    ] = 1,
    agent_message: Annotated[
        str,
        Field(description="Concise message from ModuleRuntimeQualityAgent (ignored by the tool)."),
    ] = "",
    context_variables: Annotated[
        Any | None,
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> dict[str, Any]:
    """Audit generated module runtime code and set AG2 handoff routing state."""

    code_files = _context_get(context_variables, "code_files", []) or []
    if not isinstance(code_files, list):
        code_files = []

    # Runtime warnings must reflect the current ServiceAgent output only.  Do
    # not merge prior warnings, or a fixed revision would remain blocked by
    # stale audit state from the previous pass.
    warnings = audit_module_runtime_quality(code_files)
    backend_file_count = count_module_backend_files(code_files)

    prior_attempts = _as_int(
        _context_get(context_variables, "module_runtime_quality_revision_count", 0), 0
    )
    max_attempts = max(0, _as_int(max_revision_attempts, 1))

    if not warnings:
        status = "passed"
        revision_count = prior_attempts
        revision_request = None
    elif prior_attempts < max_attempts:
        status = "needs_revision"
        revision_count = prior_attempts + 1
        revision_request = (
            "Revise the generated module backend before frontend/controller work. "
            "Remove placeholder runtime facts and make stats/counts repo-backed or "
            "return honest empty values with null trends:\n- "
            + "\n- ".join(warnings)
        )
    else:
        status = "blocked"
        revision_count = prior_attempts
        revision_request = (
            "Module runtime quality warnings remain after the allowed automated "
            "revision attempts. User/operator review is required:\n- "
            + "\n- ".join(warnings)
        )

    result: dict[str, Any] = {
        "status": status,
        "warnings": warnings,
        "module_backend_file_count": backend_file_count,
        "revision_count": revision_count,
        "max_revision_attempts": max_attempts,
        "revision_request": revision_request,
    }

    _context_set(context_variables, "module_runtime_quality_status", status)
    _context_set(context_variables, "module_runtime_quality_warnings", warnings)
    _context_set(context_variables, "module_runtime_quality_revision_count", revision_count)
    _context_set(context_variables, "module_runtime_quality_revision_request", revision_request)
    _context_set(context_variables, "module_runtime_quality_result", result)

    if warnings:
        logger.warning("[ModuleRuntimeQualityAgent] Runtime quality warnings: %s", warnings)

    return result


__all__ = [
    "audit_module_runtime_quality",
    "count_module_backend_files",
    "review_module_runtime_quality",
]

