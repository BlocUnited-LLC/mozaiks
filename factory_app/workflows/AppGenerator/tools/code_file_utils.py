"""
AppGenerator code file utilities.

Wraps mozaiksai.core.workflow.generator_support.code_files with
AppGenerator-specific payload expansion: app_backend_admin_config.

Import from here (not the runtime module) in any AppGenerator tool that
needs full payload materialization including admin surface codegen.
"""
from __future__ import annotations

from typing import Any, Dict, List

from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_map_from_payload as _base_extract,
    safe_relpath,
)
from factory_app.workflows.AppGenerator.tools.app_backend_admin_codegen import (
    build_app_backend_admin_code_files,
)


def extract_code_file_map_from_payload(payload: Any) -> Dict[str, str]:
    """Materialize all code files from an AppGenerator structured output payload.

    Calls the runtime's generic extraction, then layers in AppGenerator-specific
    typed surfaces. Currently: app_backend_admin_config → backend/admin_config.py
    + backend/routes/admin.py. The typed config wins over any conflicting raw
    code_files entries for those canonical paths.
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

    return file_map


def extract_code_file_entries_from_payload(payload: Any) -> List[Dict[str, str]]:
    file_map = extract_code_file_map_from_payload(payload)
    return [{"filename": name, "content": content} for name, content in sorted(file_map.items())]


__all__ = [
    "extract_code_file_entries_from_payload",
    "extract_code_file_map_from_payload",
    "safe_relpath",
]
