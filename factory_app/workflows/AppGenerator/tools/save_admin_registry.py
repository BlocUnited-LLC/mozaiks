"""
Tool: Save Admin Registry

Called automatically after AdminRegistryAgent speaks (auto_tool_call: true).

Persists AdminRegistryOutput to context_variables:
  - `admin_registry` — the parsed AdminRegistry dict for downstream validation
    (used by the module contract quality gate to validate panel page refs)
  - Merges `code_files` into the accumulated context code_files so that
    admin/admin_registry.yaml is available to AssemblyAgent and downstream tools.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import yaml
from pydantic import Field

from factory_app.workflows._shared.generated_ui_contract import audit_app_ui_bundle_integrity
from mozaiksai.core.admin.registry import AdminRegistry
from mozaiksai.core.workflow.context.frozen import detach

from .task_integrity import mark_repair_responded, validate_repair_candidate

logger = logging.getLogger(__name__)


def _context_get(context_variables: Any | None, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            value = context_variables.get(key)
            # Live containers freeze reads; a tuple of code files failed the
            # list test below and every earlier file was dropped from the merge.
            return default if value is None else detach(value)
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _context_set(context_variables: Any | None, key: str, value: Any) -> None:
    if context_variables is None:
        return
    if hasattr(context_variables, "set"):
        try:
            context_variables.set(key, value)
            return
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        data[key] = value
        return
    if isinstance(context_variables, dict):
        context_variables[key] = value


def save_admin_registry(
    *,
    admin_registry: Annotated[
        dict[str, Any] | None,
        Field(description="AdminRegistry object produced by AdminRegistryAgent."),
    ] = None,
    code_files: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "Serialized code files emitted by AdminRegistryAgent, "
                "always including admin/admin_registry.yaml."
            )
        ),
    ] = None,
    agent_message: Annotated[
        str | None,
        Field(description="Short summary from AdminRegistryAgent."),
    ] = None,
    context_variables: Annotated[
        Any | None,
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> str:
    """Persist AdminRegistryOutput to context.

    Saves ``admin_registry`` so the module contract quality gate can validate
    that every admin.yaml panel ``page`` field references a declared page id.

    Merges ``code_files`` into the running accumulator so AssemblyAgent picks
    up admin/admin_registry.yaml without a separate assembly step.
    """
    submitted_registry = detach(admin_registry) or {}
    registry_path = "admin/admin_registry.yaml"
    new_files = [
        item for item in detach(code_files or [])
        if isinstance(item, dict) and str(item.get("filename") or item.get("path") or "") != registry_path
    ]
    new_files.append({"filename": registry_path, "content": yaml.safe_dump(submitted_registry, sort_keys=False)})
    registry_warnings = audit_app_ui_bundle_integrity(
        new_files,
        source_label="admin registry output",
    )
    if registry_warnings:
        raise ValueError(
            "Admin registry output failed route/component boundary validation:\n- "
            + "\n- ".join(registry_warnings)
        )

    registry = AdminRegistry.model_validate(submitted_registry).model_dump(mode="json", exclude_none=True)
    new_files[-1] = {"filename": registry_path, "content": yaml.safe_dump(registry, sort_keys=False)}

    existing = _context_get(context_variables, "code_files", []) or []
    if not isinstance(existing, list):
        existing = []
    repairing = _context_get(context_variables, "bundle_repair_target") == "AdminRegistryAgent"
    if repairing:
        baseline = _context_get(context_variables, "generated_files", {}) or {}
        baseline.update({item["filename"]: item["content"] for item in existing if isinstance(item, dict) and item.get("filename")})
        admitted = validate_repair_candidate(
            context_variables,
            {item["filename"]: item["content"] for item in new_files},
            existing=baseline,
        )
        new_files = [{"filename": name, "content": content} for name, content in admitted.items()]

    merged = [item for item in existing if isinstance(item, dict) and item.get("filename") != registry_path]
    existing_names = {str(item.get("filename") or "") for item in merged}
    added = 0
    for f in new_files:
        if not isinstance(f, dict):
            continue
        fname = str(f.get("filename") or "")
        if fname and fname not in existing_names:
            merged.append(f)
            existing_names.add(fname)
            added += 1

    _context_set(context_variables, "admin_registry", registry)
    _context_set(context_variables, "code_files", merged)
    if repairing:
        mark_repair_responded(context_variables, [item["filename"] for item in new_files])

    page_count = len(registry.get("pages") or [])
    logger.info(
        "[save_admin_registry] registry saved (%d pages); %d file(s) merged into code_files",
        page_count,
        added,
    )
    return (
        f"Admin registry saved ({page_count} pages). "
        f"{added} file(s) merged into code_files."
    )


__all__ = ["save_admin_registry"]

