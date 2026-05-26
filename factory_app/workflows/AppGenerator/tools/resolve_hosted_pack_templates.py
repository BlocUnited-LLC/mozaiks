"""
Hosted Pack Template Resolver
==============================
Resolves backend template files from hosted capability pack sources into
generated app code_files entries at assembly time.

This module is build/assembly-time only. It:
- Does NOT import from mozaiks-app or any hosted product workspace.
- Does NOT modify ModuleLoader, ModuleExecutor, or any runtime component.
- Is a complete no-op when pack_sources is null/empty (OSS mode).

Template resolution rules:
1. pack_sources must be non-empty with at least one filesystem source.
2. Only api_surface tasks with owned_paths under backend/integrations/ are expanded.
3. Template source is: {pack_source_path}/{pack_id}/{tpl_relative_path}
4. Generated path is taken directly from the task's owned_paths entry.
5. pack_id must be a safe identifier (no slashes, no .., non-empty).
6. Template paths within the manifest are resolved relative to the pack directory
   and must not escape it (path traversal prevention).
7. Packs with status other than active are skipped.
8. Missing template → HostedPackTemplateError with clear message.
9. Owned path with no matching template → HostedPackTemplateError.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_INTEGRATIONS_PREFIX = "backend/integrations/"
_MANIFEST_FILENAME = "manifest.yaml"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class HostedPackTemplateError(Exception):
    """Raised when a hosted pack template cannot be resolved."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_safe_identifier(name: str) -> bool:
    """Return True if name is safe to use as a directory component."""
    if not name or name != name.strip():
        return False
    normalized = name.replace("\\", "/")
    return "/" not in normalized and ".." not in normalized


def _read_pack_manifest(pack_source_path: Path, pack_id: str) -> dict[str, Any]:
    manifest_path = pack_source_path / pack_id / _MANIFEST_FILENAME
    if not manifest_path.exists():
        raise HostedPackTemplateError(
            f"Hosted pack manifest not found: {manifest_path}"
        )
    try:
        text = manifest_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except Exception as exc:
        raise HostedPackTemplateError(
            f"Cannot read manifest at {manifest_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise HostedPackTemplateError(
            f"Manifest at {manifest_path} is not a YAML mapping"
        )
    return data


def _is_adapter_task(task: dict[str, Any]) -> bool:
    """Return True when task is an api_surface adapter task targeting backend/integrations/."""
    if str(task.get("task_type") or "").strip() != "api_surface":
        return False
    owned_paths = task.get("owned_paths") or []
    if not isinstance(owned_paths, list):
        return False
    return any(
        str(p).replace("\\", "/").startswith(_INTEGRATIONS_PREFIX)
        for p in owned_paths
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_templates_for_task(
    task: dict[str, Any],
    pack_source_path: Path,
) -> list[dict[str, str]]:
    """
    Resolve backend template files for a single hosted adapter task.

    Args:
        task: An api_surface build task dict.
        pack_source_path: Filesystem root of the capability_packs directory.

    Returns:
        List of ``{"filename": str, "content": str}`` entries, one per resolved
        template.

    Raises:
        HostedPackTemplateError: on unsafe pack_id, missing manifest, missing
            template, unmatched owned_path, or path traversal attempt.
    """
    pack_id = str(task.get("capability_pack_id") or "").strip()
    if not pack_id:
        return []

    if not _is_safe_identifier(pack_id):
        raise HostedPackTemplateError(
            f"Unsafe pack_id '{pack_id}': must be a simple identifier without "
            "path separators or traversal sequences"
        )

    adapter_paths = [
        str(p).replace("\\", "/")
        for p in (task.get("owned_paths") or [])
        if str(p).replace("\\", "/").startswith(_INTEGRATIONS_PREFIX)
    ]
    if not adapter_paths:
        return []

    manifest = _read_pack_manifest(pack_source_path, pack_id)

    pack_section = manifest.get("pack") or {}
    status = pack_section.get("status", "active")
    if status != "active":
        logger.info("Skipping template expansion for inactive pack '%s' (status=%s)", pack_id, status)
        return []

    backend_templates = manifest.get("backend_templates") or []
    if not isinstance(backend_templates, list):
        return []

    # Map output filename → manifest-relative template path
    template_map: dict[str, str] = {}
    for tpl in backend_templates:
        tpl_str = str(tpl).strip()
        if tpl_str:
            template_map[PurePosixPath(tpl_str).name] = tpl_str

    pack_dir = pack_source_path / pack_id

    results: list[dict[str, str]] = []
    for owned_path in adapter_paths:
        out_filename = PurePosixPath(owned_path).name
        if out_filename not in template_map:
            raise HostedPackTemplateError(
                f"Pack '{pack_id}' has no backend template matching '{out_filename}'. "
                f"Available templates: {list(template_map.keys())}"
            )

        tpl_relative = template_map[out_filename]

        # Path traversal guard: resolved path must remain inside pack_dir
        tpl_path = (pack_dir / tpl_relative).resolve()
        pack_dir_resolved = pack_dir.resolve()
        try:
            tpl_path.relative_to(pack_dir_resolved)
        except ValueError:
            raise HostedPackTemplateError(
                f"Path traversal detected in template path '{tpl_relative}' "
                f"for pack '{pack_id}'"
            )

        if not tpl_path.exists():
            raise HostedPackTemplateError(
                f"Backend template file not found: {tpl_path}"
            )

        content = tpl_path.read_text(encoding="utf-8")
        results.append({"filename": owned_path, "content": content})
        logger.info(
            "Resolved hosted pack template: %s -> %s",
            tpl_path,
            owned_path,
        )

    return results


def resolve_hosted_pack_templates(
    pack_sources: list[dict[str, Any]] | None,
    build_tasks: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """
    Resolve all hosted pack templates for adapter tasks in build_tasks.

    No-op when pack_sources is null or empty (OSS mode).

    Args:
        pack_sources: Pack source descriptors from context_variables (may be
            null in OSS contexts).
        build_tasks: Build task list from the AppBuildPlan. Each adapter task
            with a hosted pack template will produce a code_files entry.

    Returns:
        List of ``{"filename": str, "content": str}`` entries for all resolved
        template files.  Empty list in OSS mode.

    Raises:
        HostedPackTemplateError: if a template is declared but cannot be
            resolved.
    """
    if not pack_sources:
        return []
    if not build_tasks:
        return []

    # Find the filesystem source root
    pack_source_path: Path | None = None
    for src in pack_sources:
        if isinstance(src, dict) and src.get("kind") == "filesystem":
            raw_path = src.get("path")
            if raw_path:
                pack_source_path = Path(raw_path)
                break

    if pack_source_path is None:
        logger.info(
            "No filesystem pack source found in pack_sources — skipping template expansion"
        )
        return []

    results: list[dict[str, str]] = []
    for task in build_tasks:
        if not isinstance(task, dict):
            continue
        if not _is_adapter_task(task):
            continue
        files = resolve_templates_for_task(task, pack_source_path)
        results.extend(files)

    return results


__all__ = [
    "HostedPackTemplateError",
    "resolve_hosted_pack_templates",
    "resolve_templates_for_task",
]
