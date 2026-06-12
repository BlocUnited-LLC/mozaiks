from __future__ import annotations

import json
import logging
import re
from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section
from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema
from factory_app.workflows.AppGenerator.tools.ui_quality import review_ui_quality

logger = logging.getLogger(__name__)

_HEADER = "[APP UI QUALITY GATE]"


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


def _latest_app_schema_output(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        sender = str(message.get("name") or message.get("sender") or message.get("agent") or "")
        if sender and sender != "AppSchemaAgent":
            continue
        structured = message.get("structured_output")
        if isinstance(structured, dict) and "manifest" in structured and "pages" in structured:
            return structured
        parsed = _parse_json_object(message.get("content"))
        if isinstance(parsed, dict) and "manifest" in parsed and "pages" in parsed:
            return parsed
    return None


def _append_warning(context_variables: Any | None, warning: str) -> None:
    current = _context_get(context_variables, "app_ui_quality_warnings", [])
    warnings = list(current) if isinstance(current, list) else []
    warnings.append(warning)
    _context_set(context_variables, "app_ui_quality_warnings", warnings)


def _persist_latest_schema_if_needed(agent: Any, messages: list[dict[str, Any]]) -> None:
    context_variables = getattr(agent, "context_variables", None)
    if _context_get(context_variables, "app_schema_ready", False) is True:
        return

    payload = _latest_app_schema_output(messages)
    if not payload:
        _append_warning(
            context_variables,
            "AppSchemaAgent did not emit a parseable AppSchemaOutput before UI quality review.",
        )
        return

    try:
        save_app_schema(
            manifest=payload.get("manifest"),
            pages=payload.get("pages") or [],
            custom_route_bundle=payload.get("custom_route_bundle"),
            theme_config_patch=payload.get("theme_config_patch"),
            shell_config=payload.get("shell_config"),
            asset_manifest=payload.get("asset_manifest"),
            context_variables=context_variables,
        )
    except Exception as exc:
        logger.error("Failed to persist AppSchemaOutput before UI quality review: %s", exc)
        _append_warning(
            context_variables,
            f"AppSchemaOutput could not be persisted before UI quality review: {exc}",
        )


def run_app_ui_quality_gate(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Run the AppGenerator UI quality gate before AppUIQualityAgent replies.

    AG2 evaluates handoff conditions immediately after the agent reply. Auto-tool
    dispatch happens later in stream processing, so this hook sets the context
    variables the handoff rules need before the reply is generated.
    """

    if getattr(agent, "name", "") != "AppUIQualityAgent":
        return

    context_variables = getattr(agent, "context_variables", None)
    _persist_latest_schema_if_needed(agent, messages)
    result = review_ui_quality(context_variables=context_variables)
    status = result.get("status")
    warnings = result.get("warnings") or []
    body = (
        f"Deterministic quality gate already ran.\n"
        f"- app_ui_quality_status: {status}\n"
        f"- warning_count: {len(warnings)}\n"
        "Emit the AppUIQualityReviewRequest JSON only; do not re-review manually."
    )
    update_agent_section(agent, _HEADER, body)


__all__ = ["run_app_ui_quality_gate"]

