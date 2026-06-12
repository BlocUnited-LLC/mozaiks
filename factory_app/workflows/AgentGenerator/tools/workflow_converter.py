# ==============================================================================
# FILE: workflows/AgentGenerator/tools/workflow_converter.py
# DESCRIPTION: Workflow promotion helper and normalization contract utilities
#              for the AgentGenerator workflow.
# ==============================================================================

import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from mozaiksai.core.workflow.workflow_ui_catalog import (
    get_workflow_shipped_component_map,
    infer_workflow_ui_realization,
)

RUNTIME_EXTENSION_KINDS = {"api_router", "startup_service"}
ORCHESTRATOR_TRIGGER_TYPES = {"chat", "event", "route", "action", "schedule"}
TRANSITION_CONDITION_TYPES = {"context_equals", "context_expression", "tool_called"}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def _resolve_generated_artifacts_root() -> Path:
    raw = os.getenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", "generated").strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate.resolve()


def _context_get(context_variables: Optional[Any], key: str) -> Optional[Any]:
    if context_variables is None:
        return None
    if hasattr(context_variables, "get"):
        try:
            value = context_variables.get(key)
            if value is not None:
                return value
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    if isinstance(context_variables, dict):
        return context_variables.get(key)
    return None


def _safe_path_segment(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    return text or fallback


def _resolve_artifact_ids(
    *,
    data: Optional[Dict[str, Any]] = None,
    context_variables: Optional[Any] = None,
) -> tuple[str, str]:
    data = data or {}
    app_id = (
        _context_get(context_variables, "app_id")
        or data.get("app_id")
        or os.getenv("MOZAIKS_APP_ID")
        or "local-app"
    )
    build_id = (
        _context_get(context_variables, "build_id")
        or data.get("build_id")
        or _context_get(context_variables, "chat_id")
        or data.get("chat_id")
        or os.getenv("MOZAIKS_BUILD_ID")
        or "local-build"
    )
    return (
        _safe_path_segment(app_id, fallback="local-app"),
        _safe_path_segment(build_id, fallback="local-build"),
    )


def _resolve_workflow_output_dir(
    workflow_name: str,
    *,
    data: Optional[Dict[str, Any]] = None,
    context_variables: Optional[Any] = None,
) -> Path:
    app_id, build_id = _resolve_artifact_ids(data=data, context_variables=context_variables)
    workflow = _safe_path_segment(workflow_name, fallback="GeneratedWorkflow")
    return _resolve_generated_artifacts_root() / "workflows" / app_id / build_id / workflow


def _normalize_workflow_extra_path(raw_path: Any) -> Optional[str]:
    """Return a safe workflow-local relative path, or None if it escapes scope."""

    rel_path = str(raw_path or "").replace("\\", "/").strip().lstrip("/")
    if not rel_path or "\x00" in rel_path:
        return None

    parsed = PurePosixPath(rel_path)
    parts = parsed.parts
    if parsed.is_absolute() or not parts:
        return None
    if any(part in {"", ".", "..", "_shared"} for part in parts):
        return None

    return "/".join(parts)


def _normalize_runtime_extensions(
    extensions: Any,
    *,
    workflow_name: str,
    wf_logger: Any,
) -> List[Dict[str, Any]]:
    """Keep runtime extension entrypoints inside the generated workflow package."""

    if not isinstance(extensions, list):
        return []

    workflow_prefix = f"workflows.{workflow_name}.tools."
    normalized: List[Dict[str, Any]] = []
    for ext in extensions:
        if not isinstance(ext, dict):
            continue

        kind = str(ext.get("kind") or "").strip()
        if kind not in RUNTIME_EXTENSION_KINDS:
            wf_logger.warning(f"⚠️ [CREATE_WORKFLOW_FILES] Skipping unknown runtime extension kind: {kind}")
            continue

        entrypoint = ext.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            wf_logger.warning(f"⚠️ [CREATE_WORKFLOW_FILES] Skipping runtime extension without entrypoint: {kind}")
            continue

        entrypoint = entrypoint.strip()
        if "_shared" in entrypoint or not entrypoint.startswith(workflow_prefix):
            wf_logger.warning(
                "⚠️ [CREATE_WORKFLOW_FILES] Skipping non-local runtime extension entrypoint: %s",
                entrypoint,
            )
            continue

        normalized_ext = dict(ext)
        normalized_ext["kind"] = kind
        normalized_ext["entrypoint"] = entrypoint
        normalized.append(normalized_ext)

    return normalized


def _normalize_orchestrator_triggers(
    triggers: Any,
    *,
    wf_logger: Any,
) -> List[Dict[str, Any]]:
    """Keep orchestrator triggers within the runtime's declared trigger schema."""

    if not isinstance(triggers, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue

        item: Dict[str, Any] = {}
        trigger_type = str(trigger.get("type") or "").strip().lower()
        if trigger_type:
            if trigger_type not in ORCHESTRATOR_TRIGGER_TYPES:
                wf_logger.warning(
                    "⚠️ [CREATE_WORKFLOW_FILES] Skipping unknown orchestrator trigger type: %s",
                    trigger_type,
                )
                continue
            item["type"] = trigger_type

        for key in ("event", "endpoint", "method", "description", "capability_id"):
            value = trigger.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value:
                    item[key] = value

        if not (item.get("type") or item.get("event") or item.get("endpoint")):
            continue

        normalized.append(item)

    return normalized


def promote_generated_workflow(source_dir: str | Path, target_root: str | Path) -> Dict[str, Any]:
    """Explicitly promote a generated workflow into the active workflows root."""
    source = Path(source_dir).resolve()
    target_root_path = Path(target_root).resolve()

    if not source.is_dir():
        raise ValueError(f"Generated workflow source_dir does not exist: {source}")
    if source == target_root_path or source in target_root_path.parents:
        raise ValueError("target_root must not be the generated source_dir or inside it")

    workflow_name = _safe_path_segment(source.name, fallback="GeneratedWorkflow")
    target = target_root_path / workflow_name
    target_root_path.mkdir(parents=True, exist_ok=True)

    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    return {
        "status": "success",
        "source_dir": str(source),
        "target_root": str(target_root_path),
        "target_dir": str(target),
    }


# -----------------------------
# Normalization contract helpers
# -----------------------------


def _normalize_nullable_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.lower() in {"null", "none", "undefined"}:
        return None
    return cleaned


def _normalize_visual_agents(value: Any, *, workflow_startup_mode: Optional[str]) -> Optional[List[str]]:
    mode = str(workflow_startup_mode or "").strip().lower()
    backend_only = mode == "backendonly"

    if value is None:
        return None if backend_only else []

    normalized: List[str] = []
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in {"null", "none", "[]"}:
            return None if backend_only else []
        candidates = [segment.strip() for segment in cleaned.split(",")] if "," in cleaned else [cleaned]
        normalized = [item for item in candidates if item]
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            if not cleaned or cleaned.lower() in {"null", "none"}:
                continue
            normalized.append(cleaned)
    else:
        return None if backend_only else []

    deduped: List[str] = []
    seen = set()
    for agent_name in normalized:
        if agent_name not in seen:
            deduped.append(agent_name)
            seen.add(agent_name)

    if deduped:
        return deduped
    return None if backend_only else []


def _normalize_transition_rules(raw_rules: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_rules, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for rule in raw_rules:
        if not isinstance(rule, dict):
            continue
        item = dict(rule)
        stale_condition = _normalize_nullable_text(item.pop("condition", None))
        condition_type = _normalize_nullable_text(item.get("condition_type"))
        if condition_type is not None:
            condition_type = condition_type.lower()
        condition_key = _normalize_nullable_text(item.get("condition_key"))
        context_expression = _normalize_nullable_text(item.get("context_expression"))
        tool_name = _normalize_nullable_text(item.get("tool_name"))
        has_condition_value = "condition_value" in item
        condition_value = item.get("condition_value")

        if condition_type in {"llm", "string_llm"}:
            raise ValueError(
                "transition_graph.yaml does not support LLM-evaluated condition_type values. "
                "Use AG2-native context_equals/context_expression/tool_called routing and "
                "set routing context through a tool, structured output, or the control plane."
            )
        if condition_type == "expression" or stale_condition is not None:
            raise ValueError(
                "transition_graph.yaml no longer supports expression condition strings. "
                "Use condition_type=context_equals with condition_key/condition_value, "
                "condition_type=context_expression with context_expression, or "
                "condition_type=tool_called with tool_name."
            )

        transition_type = str(item.get("transition_type") or "").strip().lower()
        if not transition_type and condition_type:
            item["transition_type"] = "condition"
        elif not transition_type:
            item["transition_type"] = "after_turn"
        elif transition_type not in {"condition", "after_turn"}:
            raise ValueError(f"Unsupported transition_type in transition_graph.yaml: {transition_type}")
        else:
            item["transition_type"] = transition_type

        if item["transition_type"] == "after_turn":
            if (
                condition_type
                or condition_key
                or context_expression
                or tool_name
                or (has_condition_value and condition_value is not None)
            ):
                raise ValueError("after_turn transition rules must not declare condition fields")
            for key in ("condition_type", "condition_key", "condition_value", "context_expression", "tool_name"):
                item.pop(key, None)
        else:
            if condition_type not in TRANSITION_CONDITION_TYPES:
                raise ValueError(
                    "condition transition rules require condition_type=context_equals, "
                    "context_expression, or tool_called"
                )
            item["condition_type"] = condition_type
            if condition_type == "context_equals":
                if condition_key is None:
                    raise ValueError("context_equals transition rules require condition_key")
                if tool_name is not None:
                    raise ValueError("context_equals transition rules must not declare tool_name")
                if context_expression is not None:
                    raise ValueError("context_equals transition rules must not declare context_expression")
                item["condition_key"] = condition_key
                if not has_condition_value:
                    item["condition_value"] = None
                item.pop("context_expression", None)
                item.pop("tool_name", None)
            elif condition_type == "context_expression":
                if context_expression is None:
                    raise ValueError("context_expression transition rules require context_expression")
                if condition_key is not None or (has_condition_value and condition_value is not None) or tool_name is not None:
                    raise ValueError(
                        "context_expression transition rules must not declare "
                        "condition_key, condition_value, or tool_name"
                    )
                item["context_expression"] = context_expression
                for key in ("condition_key", "condition_value", "tool_name"):
                    item.pop(key, None)
            else:
                if tool_name is None:
                    raise ValueError("tool_called transition rules require tool_name")
                if (
                    condition_key is not None
                    or context_expression is not None
                    or (has_condition_value and condition_value is not None)
                ):
                    raise ValueError("tool_called transition rules must not declare context condition fields")
                item["tool_name"] = tool_name
                for key in ("condition_key", "condition_value", "context_expression"):
                    item.pop(key, None)

        normalized.append(item)
    return normalized


def _default_ui_payload_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": True}


def _normalize_ui_contract(raw_contract: Any) -> Dict[str, Any]:
    contract = raw_contract if isinstance(raw_contract, dict) else {}

    surface_kind = str(contract.get("surface_kind") or "agent_tool").strip().lower()
    if surface_kind != "agent_tool":
        surface_kind = "agent_tool"

    payload_schema = contract.get("payload_schema")
    if not isinstance(payload_schema, dict) or not payload_schema:
        payload_schema = _default_ui_payload_schema()

    actions_schema: List[Dict[str, Any]] = []
    raw_actions = contract.get("actions_schema")
    if isinstance(raw_actions, list):
        for action in raw_actions:
            if not isinstance(action, dict):
                continue
            action_id = _normalize_nullable_text(action.get("id"))
            if action_id is None:
                continue
            normalized_action: Dict[str, Any] = {"id": action_id}
            label = _normalize_nullable_text(action.get("label"))
            if label is not None:
                normalized_action["label"] = label
            description = _normalize_nullable_text(action.get("description"))
            if description is not None:
                normalized_action["description"] = description
            variant = _normalize_nullable_text(action.get("variant"))
            if variant is not None:
                normalized_action["variant"] = variant
            if isinstance(action.get("approved"), bool):
                normalized_action["approved"] = action["approved"]
            action_payload_schema = action.get("payload_schema")
            if not isinstance(action_payload_schema, dict) or not action_payload_schema:
                action_payload_schema = _default_ui_payload_schema()
            normalized_action["payload_schema"] = action_payload_schema
            actions_schema.append(normalized_action)

    return {
        "surface_kind": surface_kind,
        "payload_schema": payload_schema,
        "actions_schema": actions_schema,
    }


def _normalize_tool_type(raw_tool_type: Any) -> str:
    text = str(raw_tool_type or "").strip().lower().replace("-", "_")
    if text == "ui_tool":
        return "UI_Tool"
    if text == "ui_surface":
        return "UI_Surface"
    return "Agent_Tool"


def _normalize_tools_manifest(
    tools_manager_output: Dict[str, Any],
    wf_logger,
) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}

    raw_tools = tools_manager_output.get("tools")
    if isinstance(raw_tools, list):
        normalized_tools: List[Dict[str, Any]] = []
        for entry in raw_tools:
            if not isinstance(entry, dict):
                continue
            tool = dict(entry)
            tool.pop("integration", None)
            tool_type = _normalize_tool_type(tool.get("tool_type"))
            tool["tool_type"] = tool_type
            ui = tool.get("ui")
            if isinstance(ui, dict):
                ui = dict(ui)
                ui["realization"] = infer_workflow_ui_realization(
                    ui.get("workflow_primitive"),
                    ui.get("component"),
                )
                tool["ui"] = ui
            if tool_type == "UI_Tool":
                tool["ui_contract"] = _normalize_ui_contract(tool.get("ui_contract"))
            else:
                tool.pop("ui_contract", None)
            normalized_tools.append(tool)
        normalized["tools"] = normalized_tools
        ui_surface_count = len([t for t in normalized_tools if t.get("tool_type") in {"UI_Tool", "UI_Surface"}])
        wf_logger.info(
            "🧩 [CREATE_WORKFLOW_FILES] Normalized tools manifest entries=%d ui_surfaces=%d interactive_ui_tools=%d",
            len(normalized_tools),
            ui_surface_count,
            len([t for t in normalized_tools if t.get("tool_type") == "UI_Tool"]),
        )

    raw_lifecycle_tools = tools_manager_output.get("lifecycle_tools")
    if isinstance(raw_lifecycle_tools, list):
        normalized_lifecycle: List[Dict[str, Any]] = []
        for entry in raw_lifecycle_tools:
            if not isinstance(entry, dict):
                continue
            tool = dict(entry)
            tool.pop("integration", None)
            if "tool_type" in tool:
                tool["tool_type"] = _normalize_tool_type(tool.get("tool_type"))
            ui = tool.get("ui")
            if isinstance(ui, dict):
                ui = dict(ui)
                ui["realization"] = infer_workflow_ui_realization(
                    ui.get("workflow_primitive"),
                    ui.get("component"),
                )
                tool["ui"] = ui
            normalized_lifecycle.append(tool)
        normalized["lifecycle_tools"] = normalized_lifecycle

    return normalized


def _collect_code_files(
    output_payload: Any,
    *,
    list_key: str = "tools",
    source_name: str,
    wf_logger: Any,
) -> List[Dict[str, str]]:
    """Normalize CodeFile-style output objects into workflow extra files."""

    if not isinstance(output_payload, dict):
        return []

    raw_files = output_payload.get(list_key)
    if not isinstance(raw_files, list):
        return []

    normalized_files: List[Dict[str, str]] = []
    for index, entry in enumerate(raw_files):
        if not isinstance(entry, dict):
            wf_logger.warning(
                "⚠️ [CREATE_WORKFLOW_FILES] Skipping %s entry %d: expected object, got %s",
                source_name,
                index,
                type(entry).__name__,
            )
            continue

        rel_path = _normalize_workflow_extra_path(entry.get("filename") or entry.get("path"))
        content = entry.get("content")
        if rel_path is None:
            wf_logger.warning(
                "⚠️ [CREATE_WORKFLOW_FILES] Skipping %s entry %d with unsafe path: %r",
                source_name,
                index,
                entry.get("filename") or entry.get("path"),
            )
            continue
        if not isinstance(content, str) or not content.strip():
            wf_logger.warning(
                "⚠️ [CREATE_WORKFLOW_FILES] Skipping %s entry %d without file content: %s",
                source_name,
                index,
                rel_path,
            )
            continue

        normalized_files.append({"path": rel_path, "content": content})

    return normalized_files


def _index_workflow_ui_targets(tools_config: Any) -> Dict[str, Dict[str, Any]]:
    """Index declared workflow UI components by component name."""

    if not isinstance(tools_config, dict):
        return {}

    shipped_component_map = dict(get_workflow_shipped_component_map())
    targets: Dict[str, Dict[str, Any]] = {}
    for list_key in ("tools", "lifecycle_tools"):
        raw_entries = tools_config.get(list_key)
        if not isinstance(raw_entries, list):
            continue
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            ui = entry.get("ui")
            if not isinstance(ui, dict):
                continue
            component_name = str(ui.get("component") or "").strip()
            workflow_primitive = str(ui.get("workflow_primitive") or "").strip()
            if not component_name or not workflow_primitive or workflow_primitive == "composer_reply":
                continue
            shipped_component = shipped_component_map.get(workflow_primitive)
            realization = str(ui.get("realization") or "").strip()
            targets[component_name] = {
                "workflow_primitive": workflow_primitive,
                "shipped_component": shipped_component,
                "realization": realization,
                "direct_shipped": realization == "shipped_component",
            }
    return targets


def _build_workflow_ui_barrel(component_paths: Dict[str, str]) -> str:
    lines = [
        "/**",
        " * AUTO-GENERATED FILE - workflow UI barrel.",
        " * Each named export is auto-registered by @chat-workflows.",
        " */",
        "",
    ]
    for component_name in sorted(component_paths):
        rel_path = PurePosixPath(component_paths[component_name])
        export_path = "./" + rel_path.relative_to("ui").as_posix()
        lines.append(
            f"export {{ default as {component_name} }} from './{export_path[2:]}';"
        )
    lines.append("")
    return "\n".join(lines)


def _collect_ui_code_files(
    output_payload: Any,
    *,
    tools_config: Any,
    wf_logger: Any,
) -> List[Dict[str, str]]:
    """Collect UIFileGenerator output while enforcing shipped-primitive rules."""

    files = _collect_code_files(
        output_payload,
        source_name="UIFileGenerator",
        wf_logger=wf_logger,
    )
    if not files:
        return []

    ui_targets = _index_workflow_ui_targets(tools_config)
    workflow_component_paths: Dict[str, str] = {}
    kept_files: List[Dict[str, str]] = []
    seen_paths: set[str] = set()

    for item in files:
        rel_path = item["path"]
        if rel_path in seen_paths:
            wf_logger.warning(
                "⚠️ [CREATE_WORKFLOW_FILES] Skipping duplicate UIFileGenerator path: %s",
                rel_path,
            )
            continue
        seen_paths.add(rel_path)

        if rel_path == "ui/index.js":
            continue

        if rel_path.startswith("ui/") and PurePosixPath(rel_path).suffix.lower() in {".js", ".jsx"}:
            component_name = PurePosixPath(rel_path).stem
            target = ui_targets.get(component_name)
            if target and target.get("direct_shipped"):
                wf_logger.info(
                    "🧩 [CREATE_WORKFLOW_FILES] Skipping workflow-local React for shipped primitive "
                    "%s (%s)",
                    component_name,
                    target.get("workflow_primitive"),
                )
                continue
            if target:
                existing = workflow_component_paths.get(component_name)
                if existing and existing != rel_path:
                    raise ValueError(
                        f"UIFileGenerator emitted multiple component paths for {component_name}: "
                        f"{existing} and {rel_path}"
                    )
                workflow_component_paths[component_name] = rel_path

        kept_files.append(item)

    expected_workflow_local_components = {
        component_name
        for component_name, target in ui_targets.items()
        if not target.get("direct_shipped")
    }
    missing_components = sorted(expected_workflow_local_components - set(workflow_component_paths))
    if missing_components:
        wf_logger.warning(
            "⚠️ [CREATE_WORKFLOW_FILES] UIFileGenerator did not emit workflow-local component files for: %s",
            ", ".join(missing_components),
        )

    if workflow_component_paths:
        kept_files.append(
            {
                "path": "ui/index.js",
                "content": _build_workflow_ui_barrel(workflow_component_paths),
            }
        )

    return kept_files


__all__ = ["promote_generated_workflow"]
