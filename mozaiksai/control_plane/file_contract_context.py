"""Bounded canonical file contracts for refinement inputs."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import yaml

from mozaiksai.core.runtime.app.auth_contract import AppAuthContract
from mozaiksai.core.runtime.app.page_schema import (
    _MODULE_API_RE,
    _TOP_LEVEL_CONFIG_MODELS,
    AppPageSchema,
    PageSchemaValidationError,
    validate_page_schema,
)
from mozaiksai.core.runtime.app.paths import APP_AUTH_CONFIG_PATH

_MAX_PAGE_BYTES = 128_000
_MAX_WALK_ITEMS = 4_000
_MAX_SELECTED_PAGES = 8


def _is_page_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        path == str(PurePosixPath(path)) and "\\" not in path
        and parts[:2] == ("ui", "pages")
        and ((len(parts) == 3 and parts[2].endswith((".yaml", ".yml")))
             or (len(parts) == 4 and parts[3] in {"page.yaml", "page.yml"}))
    )


def _page_document(content: str) -> dict[str, Any] | None:
    if len(content.encode("utf-8")) > _MAX_PAGE_BYTES:
        return None
    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError:
        return None
    return raw if isinstance(raw, dict) else None


def _walk(value: Any):
    pending = [value]
    count = 0
    while pending:
        item = pending.pop()
        count += 1
        if count > _MAX_WALK_ITEMS:
            raise ValueError("Selected page exceeds the refinement context limit")
        if isinstance(item, dict):
            yield item
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def selected_file_contracts(
    *, file_map: dict[str, str], selected_paths: list[str], build_family: str,
) -> dict[str, Any]:
    """Project existing runtime model schemas; never grant write access."""
    if build_family != "app_bundle":
        return {}
    contracts: dict[str, Any] = {}
    pages = 0
    for path in dict.fromkeys(selected_paths):
        if path not in file_map:
            continue
        if path == APP_AUTH_CONFIG_PATH:
            contracts[path] = AppAuthContract.model_json_schema()
        elif _is_page_path(path):
            pages += 1
            if pages > _MAX_SELECTED_PAGES:
                contracts[path] = {"source_document_available": False,
                                   "omitted_reason": "Selected page count exceeds the refinement context limit"}
                continue
            raw = _page_document(file_map[path])
            primitives = {
                node.get("primitive") for node in _walk(raw or {})
                if isinstance(node.get("primitive"), str) and node["primitive"] in _TOP_LEVEL_CONFIG_MODELS
            }
            contracts[path] = {
                "page_schema": AppPageSchema.model_json_schema(),
                "source_document_available": raw is not None,
                "primitive_config_schemas": {
                    name: _TOP_LEVEL_CONFIG_MODELS[name].model_json_schema() for name in sorted(primitives)
                },
            }
    return contracts


def server_page_bindings(*, file_map: dict[str, str], selected_paths: list[str]) -> list[dict[str, Any]]:
    """Describe validated server-table module references as read-only context."""
    result: list[dict[str, Any]] = []
    for path in dict.fromkeys(selected_paths):
        if not _is_page_path(path) or path not in file_map:
            continue
        raw = _page_document(file_map[path])
        if raw is None:
            raise ValueError("Selected page cannot be inspected within the refinement context limit")
        try:
            page = validate_page_schema(raw)
        except PageSchemaValidationError as exc:
            raise ValueError("Selected page must satisfy the runtime schema before refinement") from exc
        for node in _walk(page.model_dump(mode="json", exclude_none=True)):
            config = node.get("config")
            if node.get("primitive") != "DataTable" or not isinstance(config, dict):
                continue
            if config.get("pagination_mode") != "server":
                continue
            endpoint = config.get("api_endpoint")
            match = _MODULE_API_RE.fullmatch(endpoint) if isinstance(endpoint, str) else None
            if match:
                module_id, action_id = match.group("module"), match.group("action")
                module_content = file_map.get(f"modules/{module_id}/module.yaml", "")
                module = _page_document(module_content) or {}
                identity = module.get("module")
                actions = module.get("actions")
                action_declared = (
                    isinstance(identity, dict) and identity.get("id") == module_id
                    and isinstance(actions, list)
                    and any(isinstance(action, dict) and action.get("id") == action_id for action in actions)
                )
                result.append({"page_path": path, "section_id": str(node.get("id") or ""),
                               "module_id": module_id, "action_id": action_id,
                               "action_declared": action_declared})
    return result
