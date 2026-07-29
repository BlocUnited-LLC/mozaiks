"""Source import contracts for App Intelligence indexing."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.app_context import safe_scan_relpath

SourceImportKind = Literal["local_workspace", "git_repository"]
GitCommandRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


class SourceImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: SourceImportKind = "local_workspace"
    workspace_root: str | None = Field(default=None, min_length=1)
    repo_url: str | None = Field(default=None, min_length=1)
    branch: str | None = Field(default=None, max_length=240)
    monorepo_path: str | None = Field(default=None, max_length=1000)
    auth_connector_id: str | None = Field(default=None, max_length=160)
    ignored_paths: list[str] = Field(default_factory=list)


class SourceImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: SourceImportKind
    workspace_root: str
    selected_root: str
    repo_url: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    monorepo_path: str | None = None
    ignored_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def resolve_source_import(
    *,
    app_id: str,
    request: SourceImportRequest | dict[str, Any],
    import_root: str | Path | None = None,
    run_git_command: GitCommandRunner | None = None,
) -> SourceImportResult:
    """Resolve a source import request into a local selected workspace root."""
    resolved = request if isinstance(request, SourceImportRequest) else SourceImportRequest.model_validate(request)
    if resolved.source_kind == "local_workspace":
        return _resolve_local_workspace(resolved)
    if resolved.source_kind == "git_repository":
        return _resolve_git_repository(
            app_id=app_id,
            request=resolved,
            import_root=import_root,
            run_git_command=run_git_command,
        )
    raise ValueError(f"Unsupported source import kind: {resolved.source_kind}")


def source_import_scan_policy(
    base: dict[str, Any] | None = None,
    *,
    ignored_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Merge import ignores into the canonical source scan policy override."""
    payload = dict(base or {})
    merged = [*list(payload.get("excluded_path_prefixes") or []), *list(payload.get("ignored_paths") or [])]
    merged.extend(ignored_paths or [])
    clean = _dedupe_safe_prefixes(merged)
    if clean:
        payload["excluded_path_prefixes"] = clean
    payload.pop("ignored_paths", None)
    return payload


def public_source_import_result(result: SourceImportResult | dict[str, Any] | None) -> dict[str, Any] | None:
    """Return source import facts safe for user-visible job payloads."""
    if result is None:
        return None
    resolved = result if isinstance(result, SourceImportResult) else SourceImportResult.model_validate(result)
    return {
        "source_kind": resolved.source_kind,
        "repo_url": resolved.repo_url,
        "branch": resolved.branch,
        "commit_sha": resolved.commit_sha,
        "monorepo_path": resolved.monorepo_path,
        "ignored_paths": list(resolved.ignored_paths),
        "workspace_root_present": bool(resolved.workspace_root),
        "selected_root_present": bool(resolved.selected_root),
        "warnings": list(resolved.warnings),
        "metadata": _public_metadata(resolved.metadata),
    }


def _resolve_local_workspace(request: SourceImportRequest) -> SourceImportResult:
    if not request.workspace_root:
        raise ValueError("workspace_root is required for local workspace indexing")
    workspace_root = Path(request.workspace_root).expanduser().resolve()
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise ValueError("workspace_root does not exist or is not a directory")
    selected_root = _selected_root(workspace_root, request.monorepo_path)
    return SourceImportResult(
        source_kind="local_workspace",
        workspace_root=workspace_root.as_posix(),
        selected_root=selected_root.as_posix(),
        monorepo_path=_normalize_monorepo_path(request.monorepo_path),
        ignored_paths=_dedupe_safe_prefixes(request.ignored_paths),
    )


def _resolve_git_repository(
    *,
    app_id: str,
    request: SourceImportRequest,
    import_root: str | Path | None,
    run_git_command: GitCommandRunner | None,
) -> SourceImportResult:
    repo_url = _validate_repo_url(request.repo_url)
    if request.auth_connector_id:
        raise ValueError("Authenticated repository imports require a connector-backed credential resolver.")
    root = _source_import_root(import_root)
    clone_dir = (root / _safe_segment(app_id) / _safe_segment(_repo_slug(repo_url))).resolve()
    try:
        clone_dir.relative_to(root)
    except ValueError as exc:
        raise ValueError("computed clone directory escapes the source import root") from exc
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    clone_dir.parent.mkdir(parents=True, exist_ok=True)

    args = ["git", "clone", "--depth", "1"]
    branch = str(request.branch or "").strip() or None
    if branch:
        args.extend(["--branch", branch])
    args.extend([repo_url, str(clone_dir)])
    runner = run_git_command or _run_git
    runner(args, root)
    commit = _git_commit(clone_dir, runner)
    selected = _selected_root(clone_dir, request.monorepo_path)
    return SourceImportResult(
        source_kind="git_repository",
        workspace_root=clone_dir.as_posix(),
        selected_root=selected.as_posix(),
        repo_url=repo_url,
        branch=branch,
        commit_sha=commit,
        monorepo_path=_normalize_monorepo_path(request.monorepo_path),
        ignored_paths=_dedupe_safe_prefixes(request.ignored_paths),
        metadata={"import_root": root.as_posix()},
    )


def _selected_root(workspace_root: Path, monorepo_path: str | None) -> Path:
    subpath = _normalize_monorepo_path(monorepo_path)
    if not subpath:
        return workspace_root
    selected = (workspace_root / subpath).resolve()
    try:
        selected.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError("monorepo_path escapes the workspace root") from exc
    if not selected.exists() or not selected.is_dir():
        raise ValueError("monorepo_path does not exist or is not a directory")
    return selected


def _validate_repo_url(value: str | None) -> str:
    repo_url = str(value or "").strip()
    if not repo_url:
        raise ValueError("repo_url is required for git repository indexing")
    parsed = urlparse(repo_url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("Only http(s) repository URLs are supported")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise ValueError("Repository URLs must not contain embedded credentials")
    if not parsed.path.strip("/"):
        raise ValueError("Repository URL is missing a repository path")
    return repo_url


def _source_import_root(import_root: str | Path | None) -> Path:
    if import_root is not None:
        candidate = Path(import_root)
    else:
        raw = os.getenv("MOZAIKS_SOURCE_IMPORTS_PATH", "generated/source_imports").strip() or "generated/source_imports"
        candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate.resolve()


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git executable is required for repository imports") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"git command failed: {message}") from exc


def _git_commit(clone_dir: Path, runner: GitCommandRunner) -> str | None:
    try:
        result = runner(["git", "-C", str(clone_dir), "rev-parse", "HEAD"], clone_dir)
    except Exception:
        return None
    return str(result.stdout or "").strip() or None


def _normalize_monorepo_path(value: str | None) -> str | None:
    if value is None:
        return None
    safe = safe_scan_relpath(value)
    return safe.rstrip("/") if safe else None


def _dedupe_safe_prefixes(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        safe = safe_scan_relpath(str(value or ""))
        if safe and safe not in out:
            out.append(safe)
    return out


def _repo_slug(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return tail.removesuffix(".git") or "repository"


def _safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip(".-") or "source"


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def _public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in dict(metadata or {}).items() if key not in {"import_root"}}


__all__ = [
    "SourceImportKind",
    "SourceImportRequest",
    "SourceImportResult",
    "public_source_import_result",
    "resolve_source_import",
    "source_import_scan_policy",
]
