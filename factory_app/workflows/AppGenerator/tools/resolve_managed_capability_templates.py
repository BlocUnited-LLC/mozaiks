"""Materialize deterministic files from selected build-context packs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Template

from mozaiksai.core.session.build_context import (
    BuildContextError,
    iter_context_assets,
    resolve_context_asset_path,
)

logger = logging.getLogger(__name__)


class ManagedCapabilityTemplateError(Exception):
    """Raised when a selected pack template tree cannot be materialized."""


def _is_safe_identifier(name: str) -> bool:
    if not name or name != name.strip():
        return False
    normalized = name.replace("\\", "/")
    return "/" not in normalized and ".." not in normalized


def _read_pack_context(pack_source_path: Path, pack_id: str) -> dict[str, Any]:
    context_path = pack_source_path / "context.yaml"
    if not context_path.exists():
        raise ManagedCapabilityTemplateError(f"Managed capability pack context not found: {context_path}")
    try:
        data = yaml.safe_load(context_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ManagedCapabilityTemplateError(f"Cannot read pack context at {context_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManagedCapabilityTemplateError(f"Pack context at {context_path} is not a YAML mapping")

    pack = data.get("pack")
    declared_id = str((pack or {}).get("id") or data.get("context_id") or "").strip() if isinstance(pack, dict) else ""
    if declared_id and declared_id != pack_id:
        raise ManagedCapabilityTemplateError(
            f"Pack context id mismatch: expected '{pack_id}', found '{declared_id}' at {context_path}"
        )
    return data


def _template_output_path(path: Path, templates_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(templates_root.resolve())
    except ValueError as exc:
        raise ManagedCapabilityTemplateError(f"Template path escapes templates root: {path}") from exc
    output_path = relative.as_posix()
    if output_path.endswith(".j2"):
        output_path = output_path[: -len(".j2")]
    if not output_path or output_path.startswith("/") or ".." in output_path.split("/"):
        raise ManagedCapabilityTemplateError(f"Unsafe template output path: {output_path}")
    return output_path


def _template_roots(context_root: Path, context: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    for asset in iter_context_assets(context, kind="templates"):
        try:
            path = resolve_context_asset_path(context_root, asset)
        except BuildContextError as exc:
            raise ManagedCapabilityTemplateError(str(exc)) from exc
        if not path.exists():
            raise ManagedCapabilityTemplateError(f"Declared pack templates asset not found: {path}")
        if not path.is_dir():
            raise ManagedCapabilityTemplateError(f"Pack templates asset must be a directory: {path}")
        roots.append(path)
    return roots


def _is_materializable_template_file(path: Path, templates_root: Path) -> bool:
    relative = path.relative_to(templates_root)
    if any(part == "__pycache__" or part.startswith(".") for part in relative.parts):
        return False
    if path.suffix in {".pyc", ".pyo"}:
        return False
    return True


def _template_variables(
    *,
    pack: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    variables: dict[str, Any] = {}
    for source in (context, pack):
        for key, value in source.items():
            if key in {"assets", "capabilities", "facades", "pack"}:
                continue
            variables[key] = value
    variables.setdefault("pack_id", str(pack.get("id") or pack.get("pack_id") or ""))
    variables.setdefault("capability_source", str(pack.get("capability_source") or ""))
    return variables


def _context_to_dict(context_variables: Any | None) -> dict[str, Any]:
    if context_variables is None:
        return {}
    if isinstance(context_variables, dict):
        return dict(context_variables)
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return dict(data)
    if hasattr(context_variables, "get"):
        try:
            keys = [
                "readiness_profile",
                "evidence_mode",
                "evidence_ledger_path",
                "launch_check_command",
                "monetization_check_command",
                "pack_source_path",
            ]
            result: dict[str, Any] = {}
            for key in keys:
                value = context_variables.get(key)
                if value is not None:
                    result[key] = value
            return result
        except Exception:
            return {}
    return {}


def resolve_templates_for_pack(
    pack_source_path: Path,
    pack_id: str,
    *,
    context_variables: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Return all files under a selected pack's ``templates/`` tree."""

    if not _is_safe_identifier(pack_id):
        raise ManagedCapabilityTemplateError(
            f"Unsafe pack_id '{pack_id}': must be a simple identifier without path separators or traversal sequences"
        )

    context_root = pack_source_path.resolve()
    context = _read_pack_context(context_root, pack_id)
    _pack_raw = context.get("pack")
    pack: dict[str, Any] = _pack_raw if isinstance(_pack_raw, dict) else {}
    if str(pack.get("status") or "active").strip() != "active":
        logger.info("Skipping template materialization for inactive pack '%s'", pack_id)
        return []

    template_vars = _template_variables(pack=pack, context={**context, **_context_to_dict(context_variables)})
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
            if path.suffix == ".j2":
                content = Template(content).render(**template_vars)
            existing = by_filename.get(output_path)
            if existing is not None and existing != content:
                raise ManagedCapabilityTemplateError(
                    f"Pack template assets contain conflicting output '{output_path}'"
                )
            by_filename[output_path] = content
    for filename, content in sorted(by_filename.items()):
        files.append({"filename": filename, "content": content})
    return files


def resolve_managed_capability_templates(
    capability_packs: list[dict[str, Any]] | None,
    *,
    context_variables: Any | None = None,
) -> list[dict[str, str]]:
    """Materialize all template files from selected managed capabilities."""

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
        for file in resolve_templates_for_pack(
            Path(raw_path),
            pack_id,
            context_variables=context_variables or {},
        ):
            filename = file["filename"]
            content = file["content"]
            existing = results_by_filename.get(filename)
            if existing is not None and existing != content:
                raise ManagedCapabilityTemplateError(
                    f"Multiple selected pack templates resolve to '{filename}'"
                )
            results_by_filename[filename] = content

    return [
        {"filename": filename, "content": content}
        for filename, content in sorted(results_by_filename.items())
    ]


__all__ = [
    "ManagedCapabilityTemplateError",
    "resolve_managed_capability_templates",
    "resolve_templates_for_pack",
]
