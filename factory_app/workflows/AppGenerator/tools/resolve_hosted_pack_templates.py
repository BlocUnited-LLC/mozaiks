"""Materialize deterministic files from selected build-context packs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from mozaiksai.core.session.build_context import (
    BuildContextError,
    iter_context_assets,
    resolve_context_asset_path,
)

logger = logging.getLogger(__name__)


class HostedPackTemplateError(Exception):
    """Raised when a selected pack template tree cannot be materialized."""


def _is_safe_identifier(name: str) -> bool:
    if not name or name != name.strip():
        return False
    normalized = name.replace("\\", "/")
    return "/" not in normalized and ".." not in normalized


def _read_pack_context(pack_source_path: Path, pack_id: str) -> dict[str, Any]:
    context_path = pack_source_path / "context.yaml"
    if not context_path.exists():
        raise HostedPackTemplateError(f"Hosted pack context not found: {context_path}")
    try:
        data = yaml.safe_load(context_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise HostedPackTemplateError(f"Cannot read pack context at {context_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise HostedPackTemplateError(f"Pack context at {context_path} is not a YAML mapping")

    pack = data.get("pack")
    declared_id = str((pack or {}).get("id") or data.get("context_id") or "").strip() if isinstance(pack, dict) else ""
    if declared_id and declared_id != pack_id:
        raise HostedPackTemplateError(
            f"Pack context id mismatch: expected '{pack_id}', found '{declared_id}' at {context_path}"
        )
    return data


def _template_output_path(path: Path, templates_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(templates_root.resolve())
    except ValueError as exc:
        raise HostedPackTemplateError(f"Template path escapes templates root: {path}") from exc
    output_path = relative.as_posix()
    if not output_path or output_path.startswith("/") or ".." in output_path.split("/"):
        raise HostedPackTemplateError(f"Unsafe template output path: {output_path}")
    return output_path


def _template_roots(context_root: Path, context: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    for asset in iter_context_assets(context, kind="templates"):
        try:
            path = resolve_context_asset_path(context_root, asset)
        except BuildContextError as exc:
            raise HostedPackTemplateError(str(exc)) from exc
        if not path.exists():
            raise HostedPackTemplateError(f"Declared pack templates asset not found: {path}")
        if not path.is_dir():
            raise HostedPackTemplateError(f"Pack templates asset must be a directory: {path}")
        roots.append(path)
    return roots


def _is_materializable_template_file(path: Path, templates_root: Path) -> bool:
    relative = path.relative_to(templates_root)
    if any(part == "__pycache__" or part.startswith(".") for part in relative.parts):
        return False
    if path.suffix in {".pyc", ".pyo"}:
        return False
    return True


def resolve_templates_for_pack(
    pack_source_path: Path,
    pack_id: str,
) -> list[dict[str, str]]:
    """Return all files under a selected pack's ``templates/`` tree."""

    if not _is_safe_identifier(pack_id):
        raise HostedPackTemplateError(
            f"Unsafe pack_id '{pack_id}': must be a simple identifier without path separators or traversal sequences"
        )

    context_root = pack_source_path.resolve()
    context = _read_pack_context(context_root, pack_id)
    pack = context.get("pack") if isinstance(context.get("pack"), dict) else {}
    if str(pack.get("status") or "active").strip() != "active":  # type: ignore[union-attr]
        logger.info("Skipping template materialization for inactive pack '%s'", pack_id)
        return []

    template_roots = _template_roots(context_root, context)
    if not template_roots:
        return []

    files: list[dict[str, str]] = []
    by_filename: dict[str, str] = {}
    for templates_root in template_roots:
        for path in sorted(
            item
            for item in templates_root.rglob("*")
            if item.is_file() and _is_materializable_template_file(item, templates_root)
        ):
            output_path = _template_output_path(path, templates_root)
            content = path.read_text(encoding="utf-8")
            existing = by_filename.get(output_path)
            if existing is not None and existing != content:
                raise HostedPackTemplateError(
                    f"Pack template assets contain conflicting output '{output_path}'"
                )
            by_filename[output_path] = content
    for filename, content in sorted(by_filename.items()):
        files.append({"filename": filename, "content": content})
    return files


def resolve_hosted_pack_templates(
    capability_packs: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Materialize all template files from selected hosted packs."""

    if not capability_packs:
        return []

    results_by_filename: dict[str, str] = {}
    for pack in capability_packs:
        if not isinstance(pack, dict):
            continue
        raw_path = pack.get("pack_source_path")
        pack_id = str(pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id") or "").strip()
        if not raw_path or not pack_id:
            continue
        for file in resolve_templates_for_pack(Path(raw_path), pack_id):
            filename = file["filename"]
            content = file["content"]
            existing = results_by_filename.get(filename)
            if existing is not None and existing != content:
                raise HostedPackTemplateError(
                    f"Multiple selected pack templates resolve to '{filename}'"
                )
            results_by_filename[filename] = content

    return [
        {"filename": filename, "content": content}
        for filename, content in sorted(results_by_filename.items())
    ]


__all__ = [
    "HostedPackTemplateError",
    "resolve_hosted_pack_templates",
    "resolve_templates_for_pack",
]
