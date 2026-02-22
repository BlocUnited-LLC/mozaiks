"""Guardrails for declarative-only generation outputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ALLOWED_TOP_LEVEL_FILES = frozenset(
    {
        "workflow_spec.yaml",
        "tool_specs.json",
        "ui_spec.json",
        "theme.tokens.json",
        "theme.css",
        "deploy.manifest.yaml",
        "generation_report.json",
    }
)
ALLOWED_TOP_LEVEL_DIRS = frozenset({"stubs", "overlays"})

_FORBIDDEN_FILENAME_PATTERNS = (
    ("sandbox_api.py", re.compile(r"(^|[\\/])sandbox_api\.py$", re.IGNORECASE)),
    ("build_events*", re.compile(r"(^|[\\/])build_events[_A-Za-z0-9.-]*$", re.IGNORECASE)),
    ("persistence*", re.compile(r"(^|[\\/])(persistence|persist)[_A-Za-z0-9.-]*$", re.IGNORECASE)),
    (
        "retry_loop*",
        re.compile(r"(^|[\\/])(retry|retry_loop|retry_processor)[_A-Za-z0-9.-]*$", re.IGNORECASE),
    ),
)

_FORBIDDEN_TOKEN_PATTERNS = (
    ("FastAPI", re.compile(r"\bFastAPI\b", re.IGNORECASE)),
    ("APIRouter", re.compile(r"\bAPIRouter\b", re.IGNORECASE)),
    ("WebSocket", re.compile(r"\bWebSocket\b", re.IGNORECASE)),
    ("e2b", re.compile(r"\be2b\b", re.IGNORECASE)),
    ("build_events", re.compile(r"\bbuild_events(?:_[A-Za-z0-9_]+)?\b", re.IGNORECASE)),
    ("runtime_extensions", re.compile(r"\bruntime_extensions\b", re.IGNORECASE)),
    ("persistence_code", re.compile(r"\b(MongoClient|pymongo|motor|sqlalchemy|asyncpg)\b", re.IGNORECASE)),
    (
        "background_retry_loop",
        re.compile(r"\b(asyncio\.create_task|while\s+True|setInterval)\b", re.IGNORECASE),
    ),
)


@dataclass(frozen=True, slots=True)
class GuardrailViolation:
    code: str
    path: str
    detail: str


def _is_allowed_output_path(relative_path: Path) -> bool:
    parts = relative_path.parts
    if not parts:
        return True
    if len(parts) == 1:
        return parts[0] in ALLOWED_TOP_LEVEL_FILES
    return parts[0] in ALLOWED_TOP_LEVEL_DIRS


def _detect_forbidden_tokens(relative_path: Path, content: str) -> list[GuardrailViolation]:
    violations: list[GuardrailViolation] = []
    for token_name, pattern in _FORBIDDEN_TOKEN_PATTERNS:
        match = pattern.search(content)
        if match is None:
            continue
        violations.append(
            GuardrailViolation(
                code="forbidden_token",
                path=relative_path.as_posix(),
                detail=f"Found forbidden token '{token_name}'.",
            )
        )
    return violations


def _detect_forbidden_filename(relative_path: Path) -> list[GuardrailViolation]:
    path_text = relative_path.as_posix()
    violations: list[GuardrailViolation] = []
    for rule_name, pattern in _FORBIDDEN_FILENAME_PATTERNS:
        if pattern.search(path_text) is None:
            continue
        violations.append(
            GuardrailViolation(
                code="forbidden_path",
                path=path_text,
                detail=f"Found forbidden generated path signature '{rule_name}'.",
            )
        )
    return violations


def validate_generated_output_tree(output_root: str | Path) -> list[GuardrailViolation]:
    root = Path(output_root)
    if not root.exists():
        return [
            GuardrailViolation(
                code="output_root_missing",
                path=root.as_posix(),
                detail="Output root does not exist.",
            )
        ]
    if not root.is_dir():
        return [
            GuardrailViolation(
                code="output_root_not_directory",
                path=root.as_posix(),
                detail="Output root must be a directory.",
            )
        ]

    violations: list[GuardrailViolation] = []

    for item in sorted(root.iterdir()):
        if not item.is_dir():
            continue
        if item.name in ALLOWED_TOP_LEVEL_DIRS:
            continue
        violations.append(
            GuardrailViolation(
                code="unexpected_directory",
                path=item.relative_to(root).as_posix(),
                detail=(
                    "Top-level directories are restricted to "
                    f"{sorted(ALLOWED_TOP_LEVEL_DIRS)}."
                ),
            )
        )

    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(root)
        violations.extend(_detect_forbidden_filename(relative_path))
        if not _is_allowed_output_path(relative_path):
            violations.append(
                GuardrailViolation(
                    code="unexpected_path",
                    path=relative_path.as_posix(),
                    detail=(
                        "Only declarative root files plus stubs/ and overlays/ "
                        "are allowed."
                    ),
                )
            )
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        violations.extend(_detect_forbidden_tokens(relative_path, content))

    return violations


__all__ = [
    "ALLOWED_TOP_LEVEL_DIRS",
    "ALLOWED_TOP_LEVEL_FILES",
    "GuardrailViolation",
    "validate_generated_output_tree",
]
