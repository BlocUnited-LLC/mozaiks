from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal

import yaml


@dataclass(frozen=True)
class GeneratedAppValidationDiagnostic:
    """Structured diagnostic emitted by generated app validation."""

    code: str
    message: str
    path: str | None = None
    severity: Literal["error", "warning"] = "error"


@dataclass(frozen=True)
class GeneratedAppValidationRequest:
    """Input to the public generated app bundle validation facade."""

    files: dict[str, str]
    capability_packs: list[dict[str, Any]] = field(default_factory=list)
    require_deployment_artifacts: bool = False
    build_tasks: list[dict[str, Any]] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class GeneratedAppValidationResult:
    """Result from the public generated app bundle validation facade."""

    passed: bool
    diagnostics: list[GeneratedAppValidationDiagnostic]


def _path_from_message(message: str) -> str | None:
    head, _, _tail = str(message).partition(":")
    candidate = head.strip()
    if "/" in candidate or "\\" in candidate or "." in PurePosixPath(candidate).name:
        return candidate or None
    return None


def _diagnostic(code: str, message: str, *, severity: Literal["error", "warning"] = "error") -> GeneratedAppValidationDiagnostic:
    return GeneratedAppValidationDiagnostic(
        code=code,
        message=message,
        path=_path_from_message(message),
        severity=severity,
    )


def _pages_from_files(files: dict[str, str]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for raw_path, content in files.items():
        path = str(raw_path or "").replace("\\", "/")
        pure = PurePosixPath(path)
        if len(pure.parts) < 3:
            continue
        if pure.parts[:2] != ("ui", "pages"):
            continue
        if pure.suffix.lower() not in {".yaml", ".yml"}:
            continue
        try:
            parsed = yaml.safe_load(str(content)) or {}
        except Exception as exc:
            pages.append(
                {
                    "name": pure.stem,
                    "page_type": None,
                    "_mozaiks_parse_error": f"{path}: page schema must be valid YAML: {exc}",
                }
            )
            continue
        if isinstance(parsed, dict):
            pages.append(parsed)
    return pages


def _validate_build_task_dependencies(build_tasks: list[dict[str, Any]]) -> list[GeneratedAppValidationDiagnostic]:
    if not build_tasks:
        return []

    from factory_app.workflows.AppGenerator.tools.app_build_plan import (
        _validate_task_dependencies,
    )

    task_ids = frozenset(str(task.get("task_id") or "") for task in build_tasks if task.get("task_id"))
    try:
        _validate_task_dependencies(build_tasks, task_ids=task_ids)
    except ValueError as exc:
        return [_diagnostic("build_task_dependency_invalid", str(exc))]
    return []


def validate_generated_app_bundle(
    request: GeneratedAppValidationRequest,
) -> GeneratedAppValidationResult:
    """Validate a generated app bundle through the stable public facade.

    This function is the supported cross-repository entrypoint for generated
    bundle validation. It intentionally wraps the AppGenerator-owned helper
    implementation instead of making each private helper a public API.
    """

    diagnostics: list[GeneratedAppValidationDiagnostic] = []

    from factory_app.workflows._shared.generated_ui_contract import audit_page_schemas
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        scan_generated_bundle,
    )

    scanner_errors = scan_generated_bundle(
        request.files,
        capability_packs=list(request.capability_packs),
        require_deployment_artifacts=request.require_deployment_artifacts,
    )
    diagnostics.extend(
        _diagnostic("generated_bundle_contract_failed", error)
        for error in scanner_errors
    )

    pages = [*request.pages, *_pages_from_files(request.files)]
    for page in pages:
        parse_error = page.get("_mozaiks_parse_error") if isinstance(page, dict) else None
        if parse_error:
            diagnostics.append(_diagnostic("page_schema_invalid", str(parse_error)))
    page_warnings = audit_page_schemas(
        [page for page in pages if isinstance(page, dict) and "_mozaiks_parse_error" not in page],
        source_label="GeneratedAppValidation",
    )
    diagnostics.extend(
        _diagnostic("page_schema_contract_warning", warning, severity="warning")
        for warning in page_warnings
    )

    diagnostics.extend(_validate_build_task_dependencies(list(request.build_tasks)))

    return GeneratedAppValidationResult(
        passed=not any(item.severity == "error" for item in diagnostics),
        diagnostics=diagnostics,
    )
