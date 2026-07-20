"""Hook: Module Runtime Quality Gate.

Fires as an prompt middleware function on ModuleRuntimeQualityAgent.

The hook parses the latest ServiceAgent structured output, merges its code_files
into context, runs the deterministic runtime audit, and injects the result into
the quality agent's system message before AG2 evaluates handoff routing.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section
from factory_app.workflows.AppGenerator.tools.code_file_utils import (
    extract_deleted_file_paths_from_payload,
)
from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
    review_module_runtime_quality,
)
from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_entries_from_payload,
)

logger = logging.getLogger(__name__)

_HEADER = "[MODULE RUNTIME QUALITY GATE]"


def _context_get(context_variables: Any | None, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            value = context_variables.get(key)
            return default if value is None else value
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


def _parse_json_object(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        return None

    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fenced:
        text = fenced.group(1)

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _latest_service_output(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        sender = str(message.get("name") or message.get("sender") or message.get("agent") or "")
        if sender and sender != "ServiceAgent":
            continue
        structured = message.get("structured_output")
        if isinstance(structured, dict) and (
            "python_files" in structured or "code_files" in structured
        ):
            return structured
        parsed = _parse_json_object(message.get("content"))
        if isinstance(parsed, dict) and ("python_files" in parsed or "code_files" in parsed):
            return parsed
    return None


def _merge_code_files(context_variables: Any | None, incoming: list[dict[str, str]]) -> None:
    if not incoming:
        return
    existing = _context_get(context_variables, "code_files", []) or []
    merged: dict[str, dict[str, str]] = {}
    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, dict):
                continue
            filename = item.get("filename") or item.get("path")
            content = item.get("content")
            if filename and content is not None:
                merged[str(filename)] = {"filename": str(filename), "content": str(content)}
    for item in incoming:
        filename = item.get("filename") or item.get("path")
        content = item.get("content")
        if filename and content is not None:
            merged[str(filename)] = {"filename": str(filename), "content": str(content)}
    _context_set(context_variables, "code_files", [merged[key] for key in sorted(merged)])


def _merge_deleted_files(context_variables: Any | None, incoming: list[str]) -> None:
    if not incoming:
        return
    existing = _context_get(context_variables, "deleted_files", []) or []
    merged: list[str] = []
    seen: set[str] = set()
    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, str) or not item.strip():
                continue
            path = item.strip()
            if path in seen:
                continue
            seen.add(path)
            merged.append(path)
    for path in incoming:
        if not path or path in seen:
            continue
        seen.add(path)
        merged.append(path)
    _context_set(context_variables, "deleted_files", merged)


def _persist_latest_service_output(agent: Any, messages: list[dict[str, Any]]) -> None:
    payload = _latest_service_output(messages)
    if not payload:
        return
    code_files = extract_code_file_entries_from_payload(payload)
    context_variables = getattr(agent, "context_variables", None)
    _merge_code_files(context_variables, code_files)
    _merge_deleted_files(context_variables, extract_deleted_file_paths_from_payload(payload))


def run_module_runtime_quality_gate(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Run the runtime quality gate before ModuleRuntimeQualityAgent replies."""

    if getattr(agent, "name", "") != "ModuleRuntimeQualityAgent":
        return

    context_variables = getattr(agent, "context_variables", None)
    _persist_latest_service_output(agent, messages)
    result = review_module_runtime_quality(context_variables=context_variables)
    status = result.get("status")
    warnings = result.get("warnings") or []
    count = result.get("module_backend_file_count", 0)

    body = (
        f"Deterministic quality gate already ran.\n"
        f"- module_runtime_quality_status: {status}\n"
        f"- module_backend_files_audited: {count}\n"
        f"- warning_count: {len(warnings)}\n"
        "Emit the ModuleRuntimeQualityReviewRequest JSON only; "
        "do not re-audit manually."
    )
    if warnings:
        body += "\n\nWarnings:\n" + "\n".join(f"  - {warning}" for warning in warnings)

    update_agent_section(agent, _HEADER, body)


__all__ = ["run_module_runtime_quality_gate"]


