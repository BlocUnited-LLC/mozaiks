"""
AppGenerator code file utilities.

Wraps mozaiksai.core.workflow.generator_support.code_files with
AppGenerator-specific payload expansion: app_backend_admin_config and
control_plane_pack.

Import from here (not the runtime module) in any AppGenerator tool that
needs full payload materialization including admin surface codegen.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from factory_app.workflows.AppGenerator.tools.app_backend_admin_codegen import (
    build_app_backend_admin_code_files,
)
from factory_app.workflows.AppGenerator.tools.control_plane_pack_codegen import (
    build_control_plane_pack_code_files,
)
from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_map_from_payload as _base_extract,
)
from mozaiksai.core.workflow.generator_support.code_files import (
    safe_relpath,
)


def extract_code_file_map_from_payload(payload: Any) -> dict[str, str]:
    """Materialize all code files from an AppGenerator structured output payload.

    Calls the runtime's generic extraction, then layers in AppGenerator-specific
    typed surfaces. Typed configs win over conflicting raw code_files entries
    for their canonical paths.
    """
    file_map = _base_extract(payload)

    if not isinstance(payload, dict):
        return file_map

    raw_admin = payload.get("app_backend_admin_config")
    if raw_admin is not None:
        for item in build_app_backend_admin_code_files(raw_admin):
            safe = safe_relpath(str(item.get("filename") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[safe] = str(content)

    raw_control_plane = payload.get("control_plane_pack")
    if raw_control_plane is not None:
        for item in build_control_plane_pack_code_files(raw_control_plane):
            safe = safe_relpath(str(item.get("filename") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[safe] = str(content)

    return file_map


def extract_code_file_entries_from_payload(payload: Any) -> list[dict[str, str]]:
    file_map = extract_code_file_map_from_payload(payload)
    return [{"filename": name, "content": content} for name, content in sorted(file_map.items())]


def collect_generated_app_file_map(
    generated_app_dir: Any,
    *,
    allowed_roots: tuple[str, ...] = ("app.json", "ui", "brand", "config"),
) -> dict[str, str]:
    """Read persisted schema artifacts from generated/apps/{app_id}/{build_id}/app.

    This is the deterministic handoff from AppSchemaAgent/save_app_schema into
    assembly, validation, and download. It intentionally collects only runtime
    app artifact roots, not logs, cache files, or workflow internals.
    """

    if not isinstance(generated_app_dir, (str, Path)):
        return {}
    root = Path(generated_app_dir).resolve()
    if not root.is_dir():
        return {}

    allowed = set(allowed_roots)
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        safe = safe_relpath(rel)
        if not safe:
            continue
        first_part = safe.split("/", 1)[0]
        if safe not in allowed and first_part not in allowed:
            continue
        try:
            files[safe] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return files


def collect_generated_app_file_entries(generated_app_dir: Any) -> list[dict[str, str]]:
    file_map = collect_generated_app_file_map(generated_app_dir)
    return [{"filename": name, "content": content} for name, content in sorted(file_map.items())]


__all__ = [
    "collect_generated_app_file_entries",
    "collect_generated_app_file_map",
    "extract_code_file_entries_from_payload",
    "extract_code_file_map_from_payload",
    "safe_relpath",
]

