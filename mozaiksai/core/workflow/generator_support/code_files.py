from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from mozaiksai.core.admin import build_app_backend_admin_code_files


def safe_relpath(raw: str) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    path = raw.replace("\\", "/").strip()
    if not path or path.startswith("/"):
        return None
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute():
        return None
    if any(part == ".." for part in pure_path.parts):
        return None
    return str(pure_path)


def _normalize_code_file_entries(raw_entries: Any) -> Dict[str, str]:
    file_map: Dict[str, str] = {}
    if not isinstance(raw_entries, list):
        return file_map

    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename") or item.get("path")
        content = item.get("content") or item.get("filecontent")
        if not filename or content is None:
            continue
        safe = safe_relpath(str(filename))
        if not safe:
            continue
        file_map[safe] = str(content)
    return file_map


def extract_code_file_map_from_payload(payload: Any) -> Dict[str, str]:
    """Resolve deterministic code files from a structured agent payload.

    `code_files` remains the generic file lane. For split app-backend admin
    surfaces, `app_backend_admin_config` is the typed source of truth and wins
    over conflicting raw code_files for the canonical admin paths.
    """

    if not isinstance(payload, dict):
        return {}

    file_map = _normalize_code_file_entries(payload.get("code_files"))

    raw_python_files = payload.get("python_files")
    if isinstance(raw_python_files, list):
        for item in raw_python_files:
            if not isinstance(item, dict):
                continue
            safe = safe_relpath(str(item.get("path") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[safe] = str(content)

    raw_database_files = payload.get("database_files")
    if isinstance(raw_database_files, list):
        for item in raw_database_files:
            if not isinstance(item, dict):
                continue
            safe = safe_relpath(str(item.get("path") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[safe] = str(content)

    raw_model_files = payload.get("model_files")
    if isinstance(raw_model_files, list):
        for item in raw_model_files:
            if not isinstance(item, dict):
                continue
            safe = safe_relpath(str(item.get("path") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[safe] = str(content)

    raw_backend_foundation_bundle = payload.get("backend_foundation_bundle")
    if isinstance(raw_backend_foundation_bundle, dict):
        raw_backend_foundation_files = raw_backend_foundation_bundle.get("files")
        if isinstance(raw_backend_foundation_files, list):
            for item in raw_backend_foundation_files:
                if not isinstance(item, dict):
                    continue
                safe = safe_relpath(str(item.get("path") or ""))
                content = item.get("content")
                if not safe or content is None:
                    continue
                file_map[safe] = str(content)

    raw_js_files = payload.get("js_files")
    if isinstance(raw_js_files, list):
        for item in raw_js_files:
            if not isinstance(item, dict):
                continue
            safe = safe_relpath(str(item.get("path") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[safe] = str(content)

    registration_barrel = payload.get("registration_barrel")
    if registration_barrel is not None:
        safe = safe_relpath("ui/index.js")
        if safe:
            file_map[safe] = str(registration_barrel)

    raw_app_backend_admin_config = payload.get("app_backend_admin_config")
    if raw_app_backend_admin_config is not None:
        for item in build_app_backend_admin_code_files(raw_app_backend_admin_config):
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
