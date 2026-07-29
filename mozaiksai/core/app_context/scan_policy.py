"""Deterministic source scanning policy for app Context Graph indexing."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

DEFAULT_CONTEXT_GRAPH_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".css",
        ".md",
    }
)
DEFAULT_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "bin",
        "obj",
        "site",
        "__pycache__",
        ".pytest_cache",
        ".release-local-venv",
        ".mypy_cache",
        ".ruff_cache",
        ".next",
        "coverage",
        ".local",
        ".logs",
        "logs",
        "generated",
        "generated_refinements",
    }
)
DEFAULT_EXCLUDED_FILE_NAMES = frozenset(
    {
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
        "poetry.lock",
        "uv.lock",
    }
)
DEFAULT_MANIFEST_PRIORITY_FILE_NAMES = frozenset(
    {
        "app.json",
        "module.yaml",
        "module.yml",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "composer.json",
        "vite.config.js",
        "vite.config.ts",
        "next.config.js",
        "next.config.mjs",
    }
)
SENSITIVE_EXACT_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".npmrc",
        ".pypirc",
    }
)
SENSITIVE_DIR_NAMES = frozenset({".ssh", "credentials", "secrets", "vault"})
SENSITIVE_NAME_FRAGMENTS = ("credential", "private_key", "secret")
SENSITIVE_SUFFIXES = (".pem", ".p12", ".pfx", ".key")


@dataclass(frozen=True)
class ScanPriorityRule:
    prefix: str
    priority: int
    label: str


@dataclass(frozen=True)
class SourceScanPolicy:
    policy_id: str = "mozaiks.context_graph.source_scan.v1"
    max_files: int = 600
    max_file_bytes: int = 180_000
    max_total_chars: int = 6_000_000
    included_extensions: frozenset[str] = DEFAULT_CONTEXT_GRAPH_EXTENSIONS
    excluded_dir_names: frozenset[str] = DEFAULT_EXCLUDED_DIR_NAMES
    excluded_file_names: frozenset[str] = DEFAULT_EXCLUDED_FILE_NAMES
    excluded_path_prefixes: frozenset[str] = frozenset()
    priority_rules: tuple[ScanPriorityRule, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SourceScanResult:
    file_map: dict[str, str]
    health: dict[str, Any]
    warnings: list[str]


DEFAULT_CONTEXT_GRAPH_PRIORITY_RULES: tuple[ScanPriorityRule, ...] = (
    ScanPriorityRule("app/modules/", 0, "app_modules"),
    ScanPriorityRule("modules/", 0, "app_modules"),
    ScanPriorityRule("app/services/", 1, "app_services"),
    ScanPriorityRule("services/", 1, "app_services"),
    ScanPriorityRule("app/ui/", 2, "app_ui"),
    ScanPriorityRule("ui/", 2, "app_ui"),
    ScanPriorityRule("app/config/", 3, "app_config"),
    ScanPriorityRule("config/", 3, "app_config"),
    ScanPriorityRule("workflows/", 4, "workflows"),
    ScanPriorityRule("control_plane/", 5, "control_plane"),
    ScanPriorityRule("build_context/contracts/", 6, "build_context_contracts"),
    ScanPriorityRule("build_context/packs/", 7, "build_context_packs"),
    ScanPriorityRule("build_context/", 8, "build_context"),
    ScanPriorityRule("factory_app/workflows/", 10, "factory_workflows"),
    ScanPriorityRule("factory_app/refinement_harness/", 11, "factory_control_plane"),
    ScanPriorityRule("mozaiksai/", 12, "runtime"),
    ScanPriorityRule("src/", 20, "src"),
    ScanPriorityRule("docs/", 70, "docs"),
    ScanPriorityRule("tests/", 80, "tests"),
    ScanPriorityRule("scripts/", 90, "scripts"),
)


def default_context_graph_scan_policy(overrides: dict[str, Any] | None = None) -> SourceScanPolicy:
    """Build the canonical Context Graph source scan policy with safe bounded overrides."""
    raw = overrides or {}
    max_files = _bounded_int(raw.get("max_files"), default=600, minimum=50, maximum=2_000)
    max_file_bytes = _bounded_int(raw.get("max_file_bytes"), default=180_000, minimum=20_000, maximum=500_000)
    max_total_chars = _bounded_int(raw.get("max_total_chars"), default=6_000_000, minimum=500_000, maximum=20_000_000)
    extensions = _normalize_extensions(raw.get("included_extensions")) or DEFAULT_CONTEXT_GRAPH_EXTENSIONS
    excluded_dirs = DEFAULT_EXCLUDED_DIR_NAMES | frozenset(_string_list(raw.get("excluded_dir_names")))
    excluded_files = DEFAULT_EXCLUDED_FILE_NAMES | frozenset(_string_list(raw.get("excluded_file_names")))
    excluded_path_prefixes = frozenset(_normalize_path_prefixes(raw.get("excluded_path_prefixes") or raw.get("ignored_paths")))
    return SourceScanPolicy(
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_chars=max_total_chars,
        included_extensions=extensions,
        excluded_dir_names=excluded_dirs,
        excluded_file_names=excluded_files,
        excluded_path_prefixes=excluded_path_prefixes,
        priority_rules=DEFAULT_CONTEXT_GRAPH_PRIORITY_RULES,
    )


def collect_source_scan_file_map(
    roots: Iterable[tuple[str, Path]],
    *,
    policy: SourceScanPolicy | None = None,
) -> SourceScanResult:
    """Collect a deterministic, bounded, secret-safe source file map from local roots."""
    scan_policy = policy or default_context_graph_scan_policy()
    candidates: list[tuple[int, str, str, Path]] = []
    skipped = Counter()
    roots_summary: list[dict[str, Any]] = []

    for label, root in roots:
        resolved_root = Path(root).expanduser().resolve()
        if not resolved_root.exists() or not resolved_root.is_dir():
            skipped["missing_root"] += 1
            continue
        root_label = str(label or "")
        roots_summary.append({"label": root_label, "path": str(resolved_root)})
        for path in _iter_source_candidates(resolved_root, scan_policy):
            try:
                rel_path = path.relative_to(resolved_root).as_posix()
            except Exception:
                skipped["unsafe_path"] += 1
                continue
            safe_rel = safe_scan_relpath(rel_path)
            if safe_rel is None:
                skipped["unsafe_path"] += 1
                continue
            reason = skip_reason_for_path(safe_rel, policy=scan_policy)
            if reason:
                skipped[reason] += 1
                continue
            graph_path = f"{root_label}/{safe_rel}" if root_label else safe_rel
            priority, rule_label = _priority_for_path(safe_rel, scan_policy)
            candidates.append((priority, rule_label, graph_path, path))

    candidates.sort(key=lambda item: (item[0], _path_depth(item[2]), item[2]))

    file_map: dict[str, str] = {}
    warnings: list[str] = []
    selected_by_priority = Counter()
    selected_by_extension = Counter()
    total_chars = 0
    limit_reached = False

    for _priority, rule_label, graph_path, path in candidates:
        if len(file_map) >= scan_policy.max_files:
            warnings.append(f"context_graph_file_limit_reached:{scan_policy.max_files}")
            limit_reached = True
            break
        try:
            size_bytes = path.stat().st_size
        except Exception:
            skipped["stat_error"] += 1
            continue
        if size_bytes > scan_policy.max_file_bytes:
            skipped["large_file"] += 1
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            skipped["read_error"] += 1
            continue
        if not text.strip():
            skipped["empty_file"] += 1
            continue
        if total_chars + len(text) > scan_policy.max_total_chars:
            warnings.append(f"context_graph_char_limit_reached:{scan_policy.max_total_chars}")
            limit_reached = True
            break
        file_map[graph_path] = text
        total_chars += len(text)
        selected_by_priority[rule_label] += 1
        selected_by_extension[PurePosixPath(graph_path).suffix.lower() or "<none>"] += 1

    if skipped.get("large_file"):
        warnings.append(f"context_graph_large_files_skipped:{skipped['large_file']}")
    if skipped.get("sensitive_path"):
        warnings.append(f"context_graph_sensitive_files_skipped:{skipped['sensitive_path']}")
    if skipped.get("excluded_path"):
        warnings.append(f"context_graph_excluded_paths_skipped:{skipped['excluded_path']}")

    health = {
        "policy_id": scan_policy.policy_id,
        "roots": roots_summary,
        "candidate_file_count": len(candidates),
        "selected_file_count": len(file_map),
        "total_chars": total_chars,
        "limit_reached": limit_reached,
        "limits": {
            "max_files": scan_policy.max_files,
            "max_file_bytes": scan_policy.max_file_bytes,
            "max_total_chars": scan_policy.max_total_chars,
        },
        "selected_by_priority": dict(sorted(selected_by_priority.items())),
        "selected_by_extension": dict(sorted(selected_by_extension.items())),
        "skipped": dict(sorted(skipped.items())),
        "warnings": list(warnings),
    }
    return SourceScanResult(file_map=file_map, health=health, warnings=warnings)


def select_source_file_map(
    file_map: dict[str, str],
    *,
    policy: SourceScanPolicy | None = None,
    source: str = "file_map",
) -> SourceScanResult:
    """Select a deterministic Context Graph source subset from an in-memory file map."""
    scan_policy = policy or default_context_graph_scan_policy()
    candidates: list[tuple[int, str, str, str]] = []
    skipped = Counter()

    for raw_path, raw_content in (file_map or {}).items():
        safe_path = safe_scan_relpath(raw_path)
        if safe_path is None:
            skipped["unsafe_path"] += 1
            continue
        reason = skip_reason_for_path(safe_path, policy=scan_policy)
        if reason:
            skipped[reason] += 1
            continue
        content = str(raw_content or "")
        size_bytes = len(content.encode("utf-8"))
        if size_bytes > scan_policy.max_file_bytes:
            skipped["large_file"] += 1
            continue
        priority, rule_label = _priority_for_path(safe_path, scan_policy)
        candidates.append((priority, rule_label, safe_path, content))

    candidates.sort(key=lambda item: (item[0], _path_depth(item[2]), item[2]))

    selected: dict[str, str] = {}
    warnings: list[str] = []
    selected_by_priority = Counter()
    selected_by_extension = Counter()
    total_chars = 0
    limit_reached = False

    for _, rule_label, path, content in candidates:
        if len(selected) >= scan_policy.max_files:
            warnings.append(f"context_graph_file_limit_reached:{scan_policy.max_files}")
            limit_reached = True
            break
        if not content.strip():
            skipped["empty_file"] += 1
            continue
        if total_chars + len(content) > scan_policy.max_total_chars:
            warnings.append(f"context_graph_char_limit_reached:{scan_policy.max_total_chars}")
            limit_reached = True
            break
        selected[path] = content
        total_chars += len(content)
        selected_by_priority[rule_label] += 1
        selected_by_extension[PurePosixPath(path).suffix.lower() or "<none>"] += 1

    if skipped.get("large_file"):
        warnings.append(f"context_graph_large_files_skipped:{skipped['large_file']}")
    if skipped.get("sensitive_path"):
        warnings.append(f"context_graph_sensitive_files_skipped:{skipped['sensitive_path']}")
    if skipped.get("excluded_path"):
        warnings.append(f"context_graph_excluded_paths_skipped:{skipped['excluded_path']}")

    health = {
        "policy_id": scan_policy.policy_id,
        "source": source,
        "candidate_file_count": len(candidates),
        "selected_file_count": len(selected),
        "total_chars": total_chars,
        "limit_reached": limit_reached,
        "limits": {
            "max_files": scan_policy.max_files,
            "max_file_bytes": scan_policy.max_file_bytes,
            "max_total_chars": scan_policy.max_total_chars,
        },
        "selected_by_priority": dict(sorted(selected_by_priority.items())),
        "selected_by_extension": dict(sorted(selected_by_extension.items())),
        "skipped": dict(sorted(skipped.items())),
        "warnings": list(warnings),
    }
    return SourceScanResult(file_map=selected, health=health, warnings=warnings)


def safe_scan_relpath(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    normalized = raw.replace("\\", "/").strip().strip("/")
    if not normalized:
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return None
    return str(path)


def skip_reason_for_path(path: str, *, policy: SourceScanPolicy | None = None) -> str | None:
    scan_policy = policy or default_context_graph_scan_policy()
    safe = safe_scan_relpath(path)
    if safe is None:
        return "unsafe_path"
    pure = PurePosixPath(safe)
    parts = tuple(part.lower() for part in pure.parts)
    if _matches_excluded_prefix(safe, scan_policy.excluded_path_prefixes):
        return "excluded_path"
    if any(part in scan_policy.excluded_dir_names for part in parts[:-1]):
        return "excluded_dir"
    name = pure.name.lower()
    if name in scan_policy.excluded_file_names or ".min." in name:
        return "excluded_file"
    if _is_sensitive_relpath(pure):
        return "sensitive_path"
    suffix = pure.suffix.lower()
    if suffix not in scan_policy.included_extensions:
        return "unsupported_extension"
    return None


def is_sensitive_source_path(path: str) -> bool:
    safe = safe_scan_relpath(path)
    return bool(safe and _is_sensitive_relpath(PurePosixPath(safe)))


def is_excluded_source_directory_path(path: str, *, policy: SourceScanPolicy | None = None) -> bool:
    scan_policy = policy or default_context_graph_scan_policy()
    safe = safe_scan_relpath(path)
    if safe is None:
        return True
    parts = tuple(part.lower() for part in PurePosixPath(safe).parts)
    return any(part in scan_policy.excluded_dir_names for part in parts[:-1])


def priority_for_source_path(path: str, *, policy: SourceScanPolicy | None = None) -> tuple[int, str]:
    safe = safe_scan_relpath(path) or ""
    return _priority_for_path(safe, policy or default_context_graph_scan_policy())


def _iter_source_candidates(root: Path, policy: SourceScanPolicy) -> Iterable[Path]:
    for current, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(
            name for name in dir_names if name.lower() not in policy.excluded_dir_names
        )
        for file_name in sorted(file_names):
            yield Path(current) / file_name


def _priority_for_path(path: str, policy: SourceScanPolicy) -> tuple[int, str]:
    normalized = safe_scan_relpath(path) or ""
    if normalized:
        normalized = f"{normalized}/" if "." not in PurePosixPath(normalized).name else normalized
    if PurePosixPath(normalized).name.lower() in DEFAULT_MANIFEST_PRIORITY_FILE_NAMES:
        return 0, "manifests"
    for rule in policy.priority_rules:
        if normalized == rule.prefix.rstrip("/") or normalized.startswith(rule.prefix):
            return rule.priority, rule.label
    return 50, "other_source"


def _path_depth(path: str) -> int:
    safe = safe_scan_relpath(path)
    return len(PurePosixPath(safe).parts) if safe else 999


def _is_sensitive_relpath(path: PurePosixPath) -> bool:
    parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    if name in SENSITIVE_EXACT_NAMES:
        return True
    if any(part in SENSITIVE_DIR_NAMES for part in parts):
        return True
    if any(fragment in name for fragment in SENSITIVE_NAME_FRAGMENTS):
        return True
    return name.endswith(SENSITIVE_SUFFIXES)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, candidate))


def _normalize_extensions(value: Any) -> frozenset[str]:
    extensions = []
    for item in _string_list(value):
        text = item.lower()
        if not text.startswith("."):
            text = f".{text}"
        extensions.append(text)
    return frozenset(extensions)


def _normalize_path_prefixes(value: Any) -> list[str]:
    prefixes: list[str] = []
    for item in _string_list(value):
        safe = safe_scan_relpath(item)
        if safe is None:
            continue
        prefix = safe.rstrip("/")
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    return prefixes


def _matches_excluded_prefix(path: str, prefixes: frozenset[str]) -> bool:
    safe = safe_scan_relpath(path)
    if not safe or not prefixes:
        return False
    normalized = safe.rstrip("/")
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in prefixes)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


__all__ = [
    "DEFAULT_CONTEXT_GRAPH_EXTENSIONS",
    "DEFAULT_MANIFEST_PRIORITY_FILE_NAMES",
    "DEFAULT_CONTEXT_GRAPH_PRIORITY_RULES",
    "SourceScanPolicy",
    "SourceScanResult",
    "ScanPriorityRule",
    "collect_source_scan_file_map",
    "default_context_graph_scan_policy",
    "is_excluded_source_directory_path",
    "is_sensitive_source_path",
    "priority_for_source_path",
    "safe_scan_relpath",
    "select_source_file_map",
    "skip_reason_for_path",
]
