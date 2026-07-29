"""Framework-aware source validation for App Intelligence contexts."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from asyncio import to_thread
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.artifacts import ArtifactStore

from .app_context import get_current_app_intelligence_snapshot
from .app_intelligence_jobs import AppIntelligenceIndexJob, get_latest_app_intelligence_index_job

try:  # pragma: no cover - import failures are represented as skipped fallback checks.
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

APP_SOURCE_VALIDATION_SCHEMA_VERSION: Literal["mozaiks.app_source_validation.v1"] = (
    "mozaiks.app_source_validation.v1"
)

AppValidationStatus = Literal["passed", "failed", "skipped", "warning"]
FrameworkValidationKind = Literal["install", "lint", "test", "build", "typecheck", "dev", "start"]
ValidationCommandRunner = Callable[
    [list[str], Path, int, dict[str, str]],
    subprocess.CompletedProcess[str],
]

DEFAULT_VALIDATION_KINDS: tuple[FrameworkValidationKind, ...] = ("lint", "typecheck", "test", "build")
_COMMAND_KIND_ORDER: dict[str, int] = {
    "install": 0,
    "lint": 1,
    "typecheck": 2,
    "test": 3,
    "build": 4,
    "dev": 90,
    "start": 91,
}
_LONG_RUNNING_KINDS = {"dev", "start"}
_SHELL_METACHAR_RE = re.compile(r"[;|&`$><\r\n]")
_ALLOWED_EXECUTABLES = {
    "composer",
    "mypy",
    "next",
    "npm",
    "npm.cmd",
    "php",
    "pnpm",
    "pnpm.cmd",
    "pytest",
    "python",
    "python3",
    "ruff",
    "tsc",
    "vite",
    "yarn",
    "yarn.cmd",
}
_COPY_EXCLUDED_DIRS = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "generated",
    "node_modules",
    "venv",
}
_COPY_EXCLUDED_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "id_dsa",
    "id_rsa",
}
_FALLBACK_EXCLUDED_PARTS = _COPY_EXCLUDED_DIRS | {"logs"}
_MAX_OVERLAY_FILES = 200
_MAX_OVERLAY_BYTES = 2_000_000
_MAX_OUTPUT_CHARS = 12_000


class AppValidationCommandCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FrameworkValidationKind
    command: str = Field(min_length=1)
    working_directory: str = "."
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AppValidationCommandPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FrameworkValidationKind
    command: str
    working_directory: str = "."
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: Literal["planned", "skipped"] = "planned"
    skip_reason: str | None = None


class AppValidationCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FrameworkValidationKind
    command: str
    working_directory: str = "."
    status: AppValidationStatus
    reason: str
    exit_code: int | None = None
    duration_ms: int = 0
    stdout_tail: str = ""
    stderr_tail: str = ""


class AppValidationFallbackCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: AppValidationStatus
    reason: str
    artifacts: list[str] = Field(default_factory=list)


class AppSourceValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.app_source_validation.v1"] = APP_SOURCE_VALIDATION_SCHEMA_VERSION
    app_id: str
    validation_status: AppValidationStatus
    source: Literal["app_intelligence_context", "explicit_workspace"]
    execution_mode: Literal["isolated_workspace_copy", "direct_workspace", "not_executed"]
    framework_detection_available: bool = False
    primary_framework_id: str | None = None
    primary_framework_label: str | None = None
    selected_kinds: list[str] = Field(default_factory=list)
    planned_commands: list[AppValidationCommandPlanItem] = Field(default_factory=list)
    command_results: list[AppValidationCommandResult] = Field(default_factory=list)
    fallback_checks: list[AppValidationFallbackCheckResult] = Field(default_factory=list)
    workspace_root_present: bool = False
    overlay_file_count: int = 0
    started_at: str
    completed_at: str
    duration_ms: int = 0
    warnings: list[str] = Field(default_factory=list)


def plan_app_source_validation_commands(
    *,
    framework_detection: dict[str, Any] | None,
    workspace_root: str | Path,
    allowed_kinds: Sequence[str] | None = None,
    include_install: bool = False,
    max_commands: int = 4,
) -> list[AppValidationCommandPlanItem]:
    """Build a safe command plan from framework detection output."""
    root = Path(workspace_root).expanduser().resolve()
    selected_kinds = _selected_kinds(allowed_kinds=allowed_kinds, include_install=include_install)
    candidates = _command_candidates(framework_detection)
    planned: list[AppValidationCommandPlanItem] = []
    seen: set[tuple[str, str, str]] = set()

    for candidate in sorted(
        candidates,
        key=lambda item: (
            _COMMAND_KIND_ORDER.get(item.kind, 999),
            -float(item.confidence or 0.0),
            item.working_directory,
            item.command,
        ),
    ):
        key = (candidate.kind, candidate.command, candidate.working_directory)
        if key in seen:
            continue
        seen.add(key)
        skip_reason = _command_skip_reason(
            candidate=candidate,
            root=root,
            selected_kinds=selected_kinds,
        )
        planned.append(
            AppValidationCommandPlanItem(
                kind=candidate.kind,
                command=candidate.command,
                working_directory=candidate.working_directory,
                confidence=candidate.confidence,
                status="skipped" if skip_reason else "planned",
                skip_reason=skip_reason,
            )
        )

    runnable_count = 0
    bounded: list[AppValidationCommandPlanItem] = []
    for item in planned:
        if item.status == "planned":
            runnable_count += 1
            if runnable_count > max(1, min(int(max_commands or 4), 12)):
                item = item.model_copy(update={"status": "skipped", "skip_reason": "max_command_count_reached"})
        bounded.append(item)
    return bounded


def run_app_source_validation(
    *,
    app_id: str,
    workspace_root: str | Path | None,
    framework_detection: dict[str, Any] | None = None,
    allowed_kinds: Sequence[str] | None = None,
    include_install: bool = False,
    max_commands: int = 4,
    timeout_seconds: int = 120,
    overlay_files: dict[str, str] | None = None,
    confirm_execution: bool = False,
    copy_workspace: bool = True,
    command_runner: ValidationCommandRunner | None = None,
    source: Literal["app_intelligence_context", "explicit_workspace"] = "explicit_workspace",
) -> AppSourceValidationResult:
    """Run framework-aware validation commands in an isolated source workspace.

    Command execution is intentionally opt-in through ``confirm_execution``.
    Detected commands run with ``shell=False`` after argv parsing, executable
    allowlisting, and working-directory containment checks.
    """
    started = datetime.now(UTC)
    start_monotonic = time.monotonic()
    resolved_app_id = str(app_id or "").strip()
    warnings: list[str] = []
    root = Path(workspace_root).expanduser().resolve() if workspace_root else None
    detection = dict(framework_detection or {})
    selected = _selected_kinds(allowed_kinds=allowed_kinds, include_install=include_install)

    if not resolved_app_id:
        raise ValueError("app_id is required")
    if root is None:
        return _validation_result(
            app_id=resolved_app_id,
            status="skipped",
            source=source,
            execution_mode="not_executed",
            framework_detection=detection,
            selected_kinds=selected,
            workspace_root_present=False,
            overlay_file_count=0,
            started=started,
            start_monotonic=start_monotonic,
            warnings=["source_workspace_unavailable"],
        )
    if not root.exists() or not root.is_dir():
        return _validation_result(
            app_id=resolved_app_id,
            status="failed",
            source=source,
            execution_mode="not_executed",
            framework_detection=detection,
            selected_kinds=selected,
            workspace_root_present=False,
            overlay_file_count=0,
            started=started,
            start_monotonic=start_monotonic,
            warnings=[f"source_workspace_missing:{root.name}"],
        )

    planned = plan_app_source_validation_commands(
        framework_detection=detection,
        workspace_root=root,
        allowed_kinds=allowed_kinds,
        include_install=include_install,
        max_commands=max_commands,
    )
    runnable = [item for item in planned if item.status == "planned"]
    warnings.extend(item.skip_reason or "" for item in planned if item.status == "skipped")
    warnings = _dedupe(warnings)

    if runnable and not confirm_execution:
        return _validation_result(
            app_id=resolved_app_id,
            status="skipped",
            source=source,
            execution_mode="not_executed",
            framework_detection=detection,
            selected_kinds=selected,
            planned_commands=planned,
            workspace_root_present=True,
            overlay_file_count=len(overlay_files or {}),
            started=started,
            start_monotonic=start_monotonic,
            warnings=[*warnings, "validation_command_execution_requires_confirmation"],
        )

    timeout = max(5, min(int(timeout_seconds or 120), 900))
    runner = command_runner or _run_subprocess_command
    command_results: list[AppValidationCommandResult] = []
    fallback_checks: list[AppValidationFallbackCheckResult] = []
    overlay_count = 0
    execution_mode: Literal["isolated_workspace_copy", "direct_workspace", "not_executed"] = "not_executed"

    if runnable:
        with _validation_workspace(root=root, copy_workspace=copy_workspace) as validation_root:
            execution_mode = "isolated_workspace_copy" if copy_workspace else "direct_workspace"
            overlay_count = _apply_overlay_files(validation_root, overlay_files)
            for item in runnable:
                command_results.append(
                    _execute_validation_command(
                        item,
                        root=validation_root,
                        timeout_seconds=timeout,
                        runner=runner,
                    )
                )
            if command_results and all(result.status == "skipped" for result in command_results):
                fallback_checks = run_app_validation_fallback_checks(validation_root)
    else:
        with _validation_workspace(root=root, copy_workspace=copy_workspace) as validation_root:
            execution_mode = "isolated_workspace_copy" if copy_workspace else "direct_workspace"
            overlay_count = _apply_overlay_files(validation_root, overlay_files)
            fallback_checks = run_app_validation_fallback_checks(validation_root)

    return _validation_result(
        app_id=resolved_app_id,
        status=_aggregate_status(command_results=command_results, fallback_checks=fallback_checks),
        source=source,
        execution_mode=execution_mode,
        framework_detection=detection,
        selected_kinds=selected,
        planned_commands=planned,
        command_results=command_results,
        fallback_checks=fallback_checks,
        workspace_root_present=True,
        overlay_file_count=overlay_count,
        started=started,
        start_monotonic=start_monotonic,
        warnings=warnings,
    )


async def run_current_app_source_validation(
    *,
    app_id: str,
    artifact_store: ArtifactStore | None = None,
    allowed_kinds: Sequence[str] | None = None,
    include_install: bool = False,
    max_commands: int = 4,
    timeout_seconds: int = 120,
    overlay_files: dict[str, str] | None = None,
    confirm_execution: bool = False,
    copy_workspace: bool = True,
) -> AppSourceValidationResult:
    """Run validation against the current App Intelligence source root."""
    resolved_app_id = str(app_id or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")

    latest_job = await get_latest_app_intelligence_index_job(app_id=resolved_app_id)
    detection = await _current_framework_detection(
        app_id=resolved_app_id,
        artifact_store=artifact_store,
        latest_job=latest_job,
    )
    workspace_root = _workspace_root_from_job(latest_job)

    return await to_thread(
        run_app_source_validation,
        app_id=resolved_app_id,
        workspace_root=workspace_root,
        framework_detection=detection,
        allowed_kinds=allowed_kinds,
        include_install=include_install,
        max_commands=max_commands,
        timeout_seconds=timeout_seconds,
        overlay_files=overlay_files,
        confirm_execution=confirm_execution,
        copy_workspace=copy_workspace,
        source="app_intelligence_context",
    )


def run_app_validation_fallback_checks(workspace_root: str | Path) -> list[AppValidationFallbackCheckResult]:
    """Run deterministic fallback checks when app commands are unavailable."""
    root = Path(workspace_root).expanduser().resolve()
    return [
        _json_manifest_check(root),
        _python_syntax_check(root),
        _yaml_manifest_check(root),
    ]


def _selected_kinds(
    *,
    allowed_kinds: Sequence[str] | None,
    include_install: bool,
) -> list[str]:
    kinds = [str(kind or "").strip().lower() for kind in allowed_kinds or [] if str(kind or "").strip()]
    if not kinds:
        kinds = list(DEFAULT_VALIDATION_KINDS)
    if not include_install:
        kinds = [kind for kind in kinds if kind != "install"]
    if include_install and "install" not in kinds:
        kinds.insert(0, "install")
    return _dedupe(kinds)


def _command_candidates(framework_detection: dict[str, Any] | None) -> list[AppValidationCommandCandidate]:
    commands = (framework_detection or {}).get("validation_commands") or []
    candidates: list[AppValidationCommandCandidate] = []
    if not isinstance(commands, list):
        return candidates
    for raw in commands:
        if not isinstance(raw, dict):
            continue
        try:
            candidates.append(AppValidationCommandCandidate.model_validate(raw))
        except Exception:
            continue
    return candidates


def _command_skip_reason(
    *,
    candidate: AppValidationCommandCandidate,
    root: Path,
    selected_kinds: Sequence[str],
) -> str | None:
    if candidate.kind not in selected_kinds:
        if candidate.kind == "install":
            return "install_commands_require_include_install"
        return "command_kind_not_selected"
    if candidate.kind in _LONG_RUNNING_KINDS:
        return "long_running_command_kind_not_supported"
    if not _is_safe_command_string(candidate.command):
        return "unsafe_command_rejected"
    if _command_argv(candidate.command) is None:
        return "command_parse_failed"
    if _validation_workdir(root, candidate.working_directory) is None:
        return "working_directory_outside_workspace"
    return None


def _execute_validation_command(
    item: AppValidationCommandPlanItem,
    *,
    root: Path,
    timeout_seconds: int,
    runner: ValidationCommandRunner,
) -> AppValidationCommandResult:
    started = time.monotonic()
    argv = _command_argv(item.command)
    workdir = _validation_workdir(root, item.working_directory)
    if argv is None or workdir is None:
        return AppValidationCommandResult(
            kind=item.kind,
            command=item.command,
            working_directory=item.working_directory,
            status="skipped",
            reason="Command could not be parsed or resolved safely.",
        )
    resolved_argv, skip_reason = _resolve_command_argv(argv)
    if skip_reason:
        return AppValidationCommandResult(
            kind=item.kind,
            command=item.command,
            working_directory=item.working_directory,
            status="skipped",
            reason=skip_reason,
            duration_ms=_elapsed_ms(started),
        )
    try:
        completed = runner(
            resolved_argv,
            workdir,
            timeout_seconds,
            _validation_env(),
        )
    except subprocess.TimeoutExpired:
        return AppValidationCommandResult(
            kind=item.kind,
            command=item.command,
            working_directory=item.working_directory,
            status="failed",
            reason=f"Command timed out after {timeout_seconds}s.",
            duration_ms=_elapsed_ms(started),
        )
    except Exception as exc:
        return AppValidationCommandResult(
            kind=item.kind,
            command=item.command,
            working_directory=item.working_directory,
            status="failed",
            reason=f"Command failed to start: {exc}",
            duration_ms=_elapsed_ms(started),
        )

    exit_code = int(completed.returncode or 0)
    return AppValidationCommandResult(
        kind=item.kind,
        command=item.command,
        working_directory=item.working_directory,
        status="passed" if exit_code == 0 else "failed",
        reason="Command completed successfully." if exit_code == 0 else f"Command exited with code {exit_code}.",
        exit_code=exit_code,
        duration_ms=_elapsed_ms(started),
        stdout_tail=_tail(completed.stdout),
        stderr_tail=_tail(completed.stderr),
    )


def _run_subprocess_command(
    argv: list[str],
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
    )


def _is_safe_command_string(command: str) -> bool:
    return bool(command and "\x00" not in command and not _SHELL_METACHAR_RE.search(command))


def _command_argv(command: str) -> list[str] | None:
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return None
    return argv if argv else None


def _resolve_command_argv(argv: list[str]) -> tuple[list[str], str | None]:
    executable = Path(argv[0]).name.lower()
    if executable not in _ALLOWED_EXECUTABLES:
        return argv, "executable_not_allowed"
    if executable in {"python", "python3"}:
        return [sys.executable, *argv[1:]], None
    resolved = shutil.which(argv[0])
    if resolved is None and os.name == "nt" and not executable.endswith(".cmd"):
        resolved = shutil.which(f"{argv[0]}.cmd")
    if resolved is None:
        return argv, f"executable_not_found:{argv[0]}"
    return [resolved, *argv[1:]], None


def _validation_workdir(root: Path, raw: str | None) -> Path | None:
    safe = _safe_relpath(raw or ".")
    if safe is None:
        return None
    candidate = (root / safe).resolve() if safe != "." else root
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.exists() and candidate.is_dir() else None


class _validation_workspace:
    def __init__(self, *, root: Path, copy_workspace: bool) -> None:
        self.root = root
        self.copy_workspace = copy_workspace
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        if not self.copy_workspace:
            self.path = self.root
            return self.path
        self._tmp = tempfile.TemporaryDirectory(prefix="mozaiks-app-validation-")
        destination = Path(self._tmp.name) / "workspace"
        shutil.copytree(
            self.root,
            destination,
            ignore=_copy_ignore,
            symlinks=False,
            ignore_dangling_symlinks=True,
            dirs_exist_ok=False,
        )
        self.path = destination.resolve()
        return self.path

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        lowered = name.lower()
        if lowered in _COPY_EXCLUDED_DIRS or lowered in _COPY_EXCLUDED_FILES:
            ignored.add(name)
            continue
        if lowered.endswith((".pem", ".key", ".pyc", ".pyo")):
            ignored.add(name)
    return ignored


def _apply_overlay_files(root: Path, overlay_files: dict[str, str] | None) -> int:
    if not overlay_files:
        return 0
    total_bytes = 0
    written = 0
    for raw_path, content in list(overlay_files.items())[:_MAX_OVERLAY_FILES]:
        safe = _safe_relpath(raw_path)
        if safe is None:
            continue
        text = str(content)
        total_bytes += len(text.encode("utf-8", errors="replace"))
        if total_bytes > _MAX_OVERLAY_BYTES:
            break
        destination = (root / safe).resolve()
        try:
            destination.relative_to(root)
        except ValueError:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        written += 1
    return written


def _safe_relpath(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    normalized = raw.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/"):
        return None
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return None
    return "." if str(path) in {"", "."} else path.as_posix()


def _json_manifest_check(root: Path) -> AppValidationFallbackCheckResult:
    artifacts: list[str] = []
    failures: list[str] = []
    for path in _iter_files(root, suffixes=(".json",), limit=120):
        if path.name not in {"package.json", "tsconfig.json", "composer.json"} and not path.name.endswith(".schema.json"):
            continue
        relative = path.relative_to(root).as_posix()
        artifacts.append(relative)
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"{relative}: {exc}")
    if failures:
        return AppValidationFallbackCheckResult(
            name="json_manifest_parse",
            status="failed",
            reason="JSON manifest parse failed: " + "; ".join(failures[:6]),
            artifacts=artifacts,
        )
    if artifacts:
        return AppValidationFallbackCheckResult(
            name="json_manifest_parse",
            status="passed",
            reason="JSON manifests parse successfully.",
            artifacts=artifacts,
        )
    return AppValidationFallbackCheckResult(
        name="json_manifest_parse",
        status="skipped",
        reason="No JSON manifests were found.",
    )


def _python_syntax_check(root: Path) -> AppValidationFallbackCheckResult:
    artifacts: list[str] = []
    failures: list[str] = []
    for path in _iter_files(root, suffixes=(".py",), limit=600):
        relative = path.relative_to(root).as_posix()
        artifacts.append(relative)
        try:
            compile(path.read_text(encoding="utf-8"), relative, "exec")
        except SyntaxError as exc:
            failures.append(f"{relative}:{exc.lineno or 0}: {exc.msg}")
        except Exception as exc:
            failures.append(f"{relative}: {exc}")
    if failures:
        return AppValidationFallbackCheckResult(
            name="python_syntax",
            status="failed",
            reason="Python syntax check failed: " + "; ".join(failures[:8]),
            artifacts=artifacts[:80],
        )
    if artifacts:
        return AppValidationFallbackCheckResult(
            name="python_syntax",
            status="passed",
            reason="Python source files compile successfully.",
            artifacts=artifacts[:80],
        )
    return AppValidationFallbackCheckResult(
        name="python_syntax",
        status="skipped",
        reason="No Python files were found.",
    )


def _yaml_manifest_check(root: Path) -> AppValidationFallbackCheckResult:
    if yaml is None:
        return AppValidationFallbackCheckResult(
            name="yaml_manifest_parse",
            status="skipped",
            reason="PyYAML is unavailable.",
        )
    artifacts: list[str] = []
    failures: list[str] = []
    for path in _iter_files(root, suffixes=(".yaml", ".yml"), limit=220):
        relative = path.relative_to(root).as_posix()
        artifacts.append(relative)
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"{relative}: {exc}")
    if failures:
        return AppValidationFallbackCheckResult(
            name="yaml_manifest_parse",
            status="failed",
            reason="YAML manifest parse failed: " + "; ".join(failures[:8]),
            artifacts=artifacts[:80],
        )
    if artifacts:
        return AppValidationFallbackCheckResult(
            name="yaml_manifest_parse",
            status="passed",
            reason="YAML manifests parse successfully.",
            artifacts=artifacts[:80],
        )
    return AppValidationFallbackCheckResult(
        name="yaml_manifest_parse",
        status="skipped",
        reason="No YAML manifests were found.",
    )


def _iter_files(root: Path, *, suffixes: tuple[str, ...], limit: int) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if len(files) >= limit:
            break
        if not path.is_file() or path.is_symlink():
            continue
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            continue
        lowered_parts = {part.lower() for part in relative_parts}
        if lowered_parts & _FALLBACK_EXCLUDED_PARTS:
            continue
        if path.suffix.lower() in suffixes:
            files.append(path)
    return files


async def _current_framework_detection(
    *,
    app_id: str,
    artifact_store: ArtifactStore | None,
    latest_job: AppIntelligenceIndexJob | None,
) -> dict[str, Any]:
    current = await get_current_app_intelligence_snapshot(app_id=app_id, artifact_store=artifact_store)
    if current.snapshot is not None:
        detection = current.snapshot.architecture.get("framework_detection")
        if isinstance(detection, dict):
            return dict(detection)
    job_detection = ((latest_job.app_intelligence or {}) if latest_job is not None else {}).get("framework_detection")
    return dict(job_detection or {}) if isinstance(job_detection, dict) else {}


def _workspace_root_from_job(job: AppIntelligenceIndexJob | None) -> str | None:
    if job is None:
        return None
    if str(job.workspace_root or "").strip():
        return str(job.workspace_root)
    import_result = job.import_result or {}
    selected = import_result.get("selected_root") if isinstance(import_result, dict) else None
    return str(selected or "").strip() or None


def _aggregate_status(
    *,
    command_results: Sequence[AppValidationCommandResult],
    fallback_checks: Sequence[AppValidationFallbackCheckResult],
) -> AppValidationStatus:
    statuses = [item.status for item in command_results] + [item.status for item in fallback_checks]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "passed" for status in statuses):
        return "passed"
    if any(status == "warning" for status in statuses):
        return "warning"
    return "skipped"


def _validation_result(
    *,
    app_id: str,
    status: AppValidationStatus,
    source: Literal["app_intelligence_context", "explicit_workspace"],
    execution_mode: Literal["isolated_workspace_copy", "direct_workspace", "not_executed"],
    framework_detection: dict[str, Any],
    selected_kinds: Sequence[str],
    workspace_root_present: bool,
    overlay_file_count: int,
    started: datetime,
    start_monotonic: float,
    planned_commands: Sequence[AppValidationCommandPlanItem] | None = None,
    command_results: Sequence[AppValidationCommandResult] | None = None,
    fallback_checks: Sequence[AppValidationFallbackCheckResult] | None = None,
    warnings: Sequence[str] | None = None,
) -> AppSourceValidationResult:
    completed = datetime.now(UTC)
    return AppSourceValidationResult(
        app_id=app_id,
        validation_status=status,
        source=source,
        execution_mode=execution_mode,
        framework_detection_available=bool(framework_detection),
        primary_framework_id=str(framework_detection.get("primary_framework_id") or "").strip() or None,
        primary_framework_label=str(framework_detection.get("primary_framework_label") or "").strip() or None,
        selected_kinds=list(selected_kinds),
        planned_commands=list(planned_commands or []),
        command_results=list(command_results or []),
        fallback_checks=list(fallback_checks or []),
        workspace_root_present=workspace_root_present,
        overlay_file_count=overlay_file_count,
        started_at=started.isoformat().replace("+00:00", "Z"),
        completed_at=completed.isoformat().replace("+00:00", "Z"),
        duration_ms=_elapsed_ms(start_monotonic),
        warnings=_dedupe([str(item or "").strip() for item in warnings or []]),
    )


def _validation_env() -> dict[str, str]:
    env = dict(os.environ)
    env["CI"] = env.get("CI") or "true"
    env["MOZAIKS_APP_VALIDATION"] = "1"
    return env


def _tail(value: Any, max_chars: int = _MAX_OUTPUT_CHARS) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _elapsed_ms(start_monotonic: float) -> int:
    return max(0, int((time.monotonic() - start_monotonic) * 1000))


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


__all__ = [
    "APP_SOURCE_VALIDATION_SCHEMA_VERSION",
    "AppSourceValidationResult",
    "AppValidationCommandCandidate",
    "AppValidationCommandPlanItem",
    "AppValidationCommandResult",
    "AppValidationFallbackCheckResult",
    "AppValidationStatus",
    "DEFAULT_VALIDATION_KINDS",
    "FrameworkValidationKind",
    "plan_app_source_validation_commands",
    "run_app_source_validation",
    "run_app_validation_fallback_checks",
    "run_current_app_source_validation",
]
