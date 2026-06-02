"""Deterministic module contract auditor.

Scans code_files for module.yaml and companion contract files, checks
schema_version markers, required fields, and cross-file consistency.

Used by the module contract quality gate tool. Pure function — no side effects.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_EXPECTED_SCHEMA_VERSIONS: Dict[str, str] = {
    "module.yaml": "mozaiks.module.v1",
    "events.yaml": "mozaiks.events.v1",
    "reactions.yaml": "mozaiks.reactions.v1",
    "notifications.yaml": "mozaiks.notifications.v1",
    "settings.yaml": "mozaiks.settings.v1",
    "admin.yaml": "mozaiks.admin.v2",
}

_VALID_MODULE_TYPES = frozenset({"standard", "messaging", "workflow", "transactional"})


def _parse_yaml_content(content: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = yaml.safe_load(content)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _audit_module_yaml(filename: str, data: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []

    expected_sv = _EXPECTED_SCHEMA_VERSIONS["module.yaml"]
    sv = data.get("schema_version")
    if sv != expected_sv:
        warnings.append(
            f"{filename}: schema_version is {sv!r}; expected {expected_sv!r}"
        )

    module_id = data.get("id") or data.get("module_id") or ""
    if not str(module_id).strip():
        warnings.append(f"{filename}: missing or empty 'id'")

    handler = str(data.get("handler") or "").strip()
    if not handler:
        warnings.append(f"{filename}: missing 'handler'")
    elif not handler.startswith("backend.handler:"):
        warnings.append(
            f"{filename}: handler {handler!r} must start with 'backend.handler:'"
        )

    module_type = data.get("type")
    if module_type and str(module_type) not in _VALID_MODULE_TYPES:
        warnings.append(
            f"{filename}: type {module_type!r} is not one of {sorted(_VALID_MODULE_TYPES)}"
        )

    actions = data.get("actions") or []
    if not isinstance(actions, list):
        warnings.append(f"{filename}: 'actions' must be a list")
    else:
        for i, action in enumerate(actions):
            if not isinstance(action, dict):
                warnings.append(f"{filename}: actions[{i}] is not a mapping")
                continue
            action_id = action.get("id") or ""
            if not str(action_id).strip():
                warnings.append(f"{filename}: actions[{i}] missing 'id'")
            if not action.get("handler_method"):
                label = str(action_id) if action_id else str(i)
                warnings.append(
                    f"{filename}: action {label!r} missing 'handler_method'"
                )

    return warnings


def _audit_contract_yaml(filename: str, data: Dict[str, Any]) -> List[str]:
    """Check schema_version for companion contract files (events, reactions, etc.)."""
    warnings: List[str] = []
    basename = PurePosixPath(filename).name
    expected_sv = _EXPECTED_SCHEMA_VERSIONS.get(basename)
    if expected_sv is not None:
        sv = data.get("schema_version")
        if sv != expected_sv:
            warnings.append(
                f"{filename}: schema_version is {sv!r}; expected {expected_sv!r}"
            )
    return warnings


def _module_dir_of(filename: str) -> str:
    """Return the modules/{module_id} prefix for a given contract file path."""
    parts = PurePosixPath(filename).parts
    # Expected shapes:
    #   modules/{id}/module.yaml  → modules/{id}
    #   modules/{id}/contracts/events.yaml  → modules/{id}
    for i, part in enumerate(parts):
        if part == "modules" and i + 1 < len(parts):
            return "/".join(parts[: i + 2])
    return str(PurePosixPath(filename).parent)


def audit_admin_panel_page_refs(
    code_files: List[Dict[str, Any]],
    valid_page_ids: set,
) -> List[str]:
    """Check that every admin.yaml panel ``page`` field references a declared registry id.

    Args:
        code_files: List of {filename, content} dicts.
        valid_page_ids: Page ids from admin/admin_registry.yaml. Empty set skips validation.

    Returns:
        List of warning strings. Empty list means no issues.
    """
    if not valid_page_ids or not code_files:
        return []

    warnings: List[str] = []
    for file_entry in code_files:
        if not isinstance(file_entry, dict):
            continue
        filename = str(file_entry.get("filename") or "").strip()
        if PurePosixPath(filename).name != "admin.yaml":
            continue
        content = file_entry.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        data = _parse_yaml_content(content)
        if data is None:
            continue
        panels = data.get("panels") or []
        if not isinstance(panels, list):
            continue
        for panel in panels:
            if not isinstance(panel, dict):
                continue
            page_ref = panel.get("page")
            panel_id = str(panel.get("id") or "<unknown>")
            if not page_ref:
                warnings.append(
                    f"{filename}: panel {panel_id!r} missing required 'page' field"
                )
            elif str(page_ref) not in valid_page_ids:
                warnings.append(
                    f"{filename}: panel {panel_id!r} unresolved admin page ref "
                    f"{page_ref!r} (valid ids: {sorted(valid_page_ids)})"
                )
    return warnings


def audit_module_contracts(code_files: List[Dict[str, Any]]) -> List[str]:
    """Audit module contract YAML files in code_files.

    Args:
        code_files: List of {filename, content} dicts from agent code output.

    Returns:
        List of warning strings. Empty list means no issues found.
    """
    warnings: List[str] = []

    if not code_files:
        return warnings

    module_yamls: Dict[str, Dict[str, Any]] = {}        # filename → parsed data
    events_by_module: Dict[str, str] = {}               # module_dir → filename
    events_type_sets: Dict[str, set] = {}               # module_dir → declared event type strings
    events_producers: Dict[str, Dict[str, str]] = {}    # module_dir → {event_type → producer}

    for file_entry in code_files:
        if not isinstance(file_entry, dict):
            continue
        filename = str(file_entry.get("filename") or "").strip()
        content = file_entry.get("content")
        if not filename or not isinstance(content, str) or not content.strip():
            continue

        basename = PurePosixPath(filename).name
        if basename not in _EXPECTED_SCHEMA_VERSIONS:
            continue

        data = _parse_yaml_content(content)
        if data is None:
            warnings.append(f"{filename}: could not parse as YAML")
            continue

        mod_dir = _module_dir_of(filename)

        if basename == "module.yaml":
            module_yamls[filename] = data
            warnings.extend(_audit_module_yaml(filename, data))
        elif basename == "events.yaml":
            events_by_module[mod_dir] = filename
            warnings.extend(_audit_contract_yaml(filename, data))
            declared: set = set()
            producers: Dict[str, str] = {}
            for ev in (data.get("events") or []):
                if isinstance(ev, dict):
                    ev_type = str(ev.get("type") or "").strip()
                    if ev_type:
                        declared.add(ev_type)
                        producers[ev_type] = str(ev.get("producer") or "").strip()
            events_type_sets[mod_dir] = declared
            events_producers[mod_dir] = producers
        else:
            warnings.extend(_audit_contract_yaml(filename, data))

    # Cross-file checks for modules that declare emits[]
    for mod_path, mod_data in module_yamls.items():
        mod_dir = _module_dir_of(mod_path)
        module_id = str(mod_data.get("id") or mod_data.get("module_id") or "").strip()
        emitting_actions = [
            a for a in (mod_data.get("actions") or [])
            if isinstance(a, dict) and a.get("emits")
        ]
        if not emitting_actions:
            continue

        if mod_dir not in events_by_module:
            emitted = [
                str(e)
                for a in emitting_actions
                for e in (a.get("emits") or [])
                if e
            ]
            if emitted:
                warnings.append(
                    f"{mod_path}: actions declare emits {emitted} "
                    f"but no events.yaml found under {mod_dir}/"
                )
            continue

        # events.yaml exists — check each emitted type is declared and producer matches
        declared_types = events_type_sets.get(mod_dir, set())
        producer_map = events_producers.get(mod_dir, {})
        for action in emitting_actions:
            action_id = str(action.get("id") or "").strip()
            for ev_type in (action.get("emits") or []):
                ev_type_str = str(ev_type).strip()
                if not ev_type_str:
                    continue
                if ev_type_str not in declared_types:
                    warnings.append(
                        f"{mod_path}: action {action_id!r} emits {ev_type_str!r} "
                        f"but that type is not declared in "
                        f"{events_by_module[mod_dir]}"
                    )
                elif module_id and producer_map.get(ev_type_str, module_id) != module_id:
                    warnings.append(
                        f"{events_by_module[mod_dir]}: event {ev_type_str!r} "
                        f"producer {producer_map[ev_type_str]!r} does not match "
                        f"module id {module_id!r}"
                    )

    return warnings


__all__ = ["audit_module_contracts", "audit_admin_panel_page_refs"]
