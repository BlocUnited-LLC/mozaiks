# ==============================================================================
# FILE: workflows/AgentGenerator/tools/workflow_converter.py
# DESCRIPTION: Self-contained workflow file creator for Generator workflow
# ==============================================================================

from typing import Dict, Any, Optional, List
from pathlib import Path, PurePosixPath
import json
import os
import shutil
import yaml
import re
import textwrap


from logs.logging_config import get_workflow_logger
from logs.runtime_artifacts import get_workflow_converter_logs_dir
from mozaiksai.core.workflow.workflow_ui_catalog import (
    get_workflow_shipped_component_map,
    infer_workflow_ui_realization,
)

# Standard YAML file mappings for workflows
WORKFLOW_FILE_MAPPINGS = {
    'orchestrator': 'orchestrator.yaml',
    'agents': 'agents.yaml',
    'handoffs': 'handoffs.yaml',
    'context_variables': 'context_variables.yaml',
    'structured_outputs': 'structured_outputs.yaml',
    'hooks': 'hooks.yaml', 
    'tools': 'tools.yaml',
    'ui_config': 'ui_config.yaml'
}

RUNTIME_EXTENSION_KINDS = {"api_router", "startup_service", "lifecycle_hooks"}
ORCHESTRATOR_TRIGGER_TYPES = {"chat", "event", "route", "action", "schedule"}


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


def _generate_capability_spec(
    workflow_name: str,
    config: Dict[str, Any],
    workflow_dir: Path
) -> Optional[str]:
    """
    Generate a capability_spec.json for the workflow so it can be discovered
    by MozaiksCore's capability loading system.
    
    Returns the path to the created file, or None if generation failed.
    """
    wf_logger = get_workflow_logger(workflow_name=workflow_name)
    
    try:
        # Extract metadata from config for capability definition
        display_name = config.get('display_name') or config.get('workflow_name') or workflow_name
        description = config.get('description') or f"AI-powered workflow: {workflow_name}"
        icon = config.get('icon') or 'robot'
        
        # Default visibility and plan access
        visibility = config.get('visibility') or 'user'
        allowed_plans = config.get('allowed_plans') or ['*']
        
        # Generate capability ID (lowercase, hyphenated)
        capability_id = workflow_name.lower().replace('_', '-').replace(' ', '-')
        
        capability_spec = {
            "capability": {
                "id": capability_id,
                "display_name": display_name,
                "description": description,
                "icon": icon,
                "workflow_id": workflow_name,
                "enabled": True,
                "visibility": visibility,
                "allowed_plans": allowed_plans
            }
        }
        
        # Save to workflow directory
        spec_path = workflow_dir / "capability_spec.json"
        with open(spec_path, 'w', encoding='utf-8') as f:
            json.dump(capability_spec, f, indent=2, ensure_ascii=False)
        
        wf_logger.info(f"📄 [SAVE] Generated capability_spec.json for workflow={workflow_name}")
        return str(spec_path)
        
    except Exception as e:
        wf_logger.error(f"❌ [SAVE] Failed to generate capability_spec.json: {e}")
        return None


def _generate_websocket_config(
    workflow_name: str,
    config: Dict[str, Any],
    workflow_dir: Path
) -> Optional[str]:
    """
    Generate websocket_config.yaml defining WebSocket endpoints for the workflow.
    
    This config is consumed by MozaiksCore to expose the workflow via WebSocket,
    and by AppGenerator to wire up client connections.
    
    Returns the path to the created file, or None if generation failed.
    """
    wf_logger = get_workflow_logger(workflow_name=workflow_name)
    
    try:
        # Generate WebSocket endpoint paths
        workflow_id_lower = workflow_name.lower().replace('_', '-').replace(' ', '-')
        
        # Extract agents to create per-agent channels if needed
        agents_config = config.get('agents', {})
        agent_names = list(agents_config.keys()) if isinstance(agents_config, dict) else []
        
        # Define primary WebSocket endpoint
        primary_endpoint = {
            "path": f"/ws/{workflow_id_lower}",
            "description": f"Primary WebSocket endpoint for {workflow_name} workflow",
            "protocol": "json",
            "auth_required": True,
            "supports_streaming": True
        }
        
        # Define chat endpoint for conversational workflows
        chat_endpoint = {
            "path": f"/ws/{workflow_id_lower}/chat",
            "description": f"Chat interface for {workflow_name}",
            "protocol": "json",
            "auth_required": True,
            "message_types": ["user_message", "agent_response", "tool_call", "tool_result", "handoff"]
        }
        
        # Define events endpoint for real-time updates
        events_endpoint = {
            "path": f"/ws/{workflow_id_lower}/events",
            "description": f"Event stream for {workflow_name} workflow state changes",
            "protocol": "json",
            "auth_required": True,
            "event_types": ["agent_start", "agent_complete", "tool_start", "tool_complete", "workflow_complete", "error"]
        }
        
        websocket_config = {
            "workflow_name": workflow_name,
            "workflow_id": workflow_id_lower,
            "endpoints": {
                "primary": primary_endpoint,
                "chat": chat_endpoint,
                "events": events_endpoint
            },
            "connection_settings": {
                "heartbeat_interval_ms": 30000,
                "reconnect_attempts": 3,
                "max_message_size_bytes": 1048576
            },
            "agent_channels": agent_names
        }
        
        # Save as YAML
        config_path = workflow_dir / "websocket_config.yaml"
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(websocket_config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        
        wf_logger.info(f"📄 [SAVE] Generated websocket_config.yaml for workflow={workflow_name}")
        wf_logger.info(f"   Primary endpoint: {primary_endpoint['path']}")
        wf_logger.info(f"   Chat endpoint: {chat_endpoint['path']}")
        wf_logger.info(f"   Events endpoint: {events_endpoint['path']}")
        
        return str(config_path)
        
    except Exception as e:
        wf_logger.error(f"❌ [SAVE] Failed to generate websocket_config.yaml: {e}")
        return None



def clean_agent_content(content: str, agent_name: str = None) -> Optional[str]:
    """
    Remove Markdown code fences and clean JSON content.
    Based on proven pattern from previous project's file_manager.py.
    
    Handles:
    - Markdown code fences (```json ... ```)
    - Language identifiers (json, JSON)
    - Trailing commas before closing brackets
    - Invalid escape sequences
    - Extra whitespace
    """
    if not content:
        return None
        
    try:
        content = content.strip()
        
        # Remove Markdown code fences (```json ... ```)
        if content.startswith("```") and content.endswith("```"):
            content = content[3:-3].strip()
        
        # Remove "json" or "JSON" prefix if present
        if content.lower().startswith("json"):
            content = content[4:].strip()
        
        # Remove trailing commas before closing brackets
        content = re.sub(r',\s*([\]}])', r'\1', content)
        
        # Detect and extract JSON correctly
        json_start = content.find("{") if "{" in content else content.find("[")
        if json_start != -1:
            content = content[json_start:]
        
        # Find the last closing bracket
        json_end = content.rfind("}") if "}" in content else content.rfind("]")
        if json_end != -1:
            content = content[:json_end + 1]
        
        # Validate final JSON format
        json.loads(content)  # Raises an exception if invalid
        return content
        
    except json.JSONDecodeError as e:
        wf_logger = get_workflow_logger()
        wf_logger.error(f"❌ [CLEAN_AGENT_CONTENT] JSON Parsing Error for {agent_name or 'unknown agent'}: {e}")
        wf_logger.error(f"❌ [CLEAN_AGENT_CONTENT] Content preview: {content[:500]}...")
        return None


def _save_yaml_file(file_path: Path, data: Dict[str, Any]) -> None:
    """Save data to a YAML file with clean formatting"""
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            indent=2,
        )


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


def _strip_markdown_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
    return stripped


def _convert_object_ids(text: str) -> str:
    return re.sub(r'ObjectId\(["\']?([0-9a-fA-F]{24})["\']?\)', r'{"$oid": "\1"}', text)


def _ensure_newlines(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def _fix_jsx_syntax(content: str) -> str:
    try:
        text = content
        if "import React" not in text:
            text = "import React from \"react\";\n" + text

        text = re.sub(r"<\s*n\s*>", "", text)
        text = re.sub(r"</\s*n\s*>", "", text)

        self_closing = r"(<(area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr)([^>]*)>)"
        text = re.sub(self_closing, lambda m: m.group(1).rstrip(">") + " />", text)
        return text
    except Exception:
        return content


def _normalize_json_text(raw: str) -> Optional[str]:
    try:
        parsed = json.loads(raw)
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    except Exception:
        return None


def _normalize_file_content(rel_path: str, content: Any, agent_name: Optional[str] = None) -> Any:
    if isinstance(content, (dict, list)):
        try:
            return json.dumps(content, indent=2, ensure_ascii=False)
        except Exception:
            return content

    if not isinstance(content, str):
        return content

    text = content.strip("\ufeff")
    text = _strip_markdown_fences(text)
    lowered = text.strip().lower()
    if lowered.startswith("json"):
        text = text.strip()[4:].lstrip()

    text = _convert_object_ids(text)
    text = textwrap.dedent(text)
    text = _ensure_newlines(text)

    suffix = Path(rel_path).suffix.lower()
    if suffix == ".json":
        normalized = _normalize_json_text(text.strip())
        if normalized is not None:
            return _ensure_newlines(normalized)

    if suffix in {".jsx", ".tsx"}:
        text = _fix_jsx_syntax(text)

    return _ensure_newlines(text)


def _split_config_into_sections(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Split a unified config into sections for separate JSON files"""
    sections = {}

    # Orchestrator section (top-level workflow settings)
    orchestrator_keys = [
        'workflow_name', 'max_turns', 'human_in_the_loop', 'startup_mode',
        'orchestration_pattern', 'initial_message_to_user', 'initial_message',
        'initial_agent', 'runtime_extensions', 'triggers'
    ]
    sections['orchestrator'] = {k: v for k, v in config.items() if k in orchestrator_keys}

    # Direct mappings
    sections['agents'] = config.get('agents', {})
    sections['handoffs'] = config.get('handoffs', {})
    sections['context_variables'] = config.get('context_variables', {})
    sections['structured_outputs'] = config.get('structured_outputs', {})
    sections['hooks'] = config.get('hooks', {})
    sections['tools'] = config.get('tools', {})
    sections['ui_config'] = {k: v for k, v in config.items() if k in ['visual_agents']}

    return sections


# -----------------------------
# Structured outputs utilities
# -----------------------------

def _normalize_model_library(models: Any) -> Dict[str, Any]:
    """Normalize model library into dict form."""
    if isinstance(models, dict):
        return dict(models)

    lib: Dict[str, Any] = {}
    if isinstance(models, list):
        for md in models:
            if not isinstance(md, dict):
                continue
            name = md.get('model_name')
            fields_list = md.get('fields') or []
            if not name or not isinstance(fields_list, list):
                continue
            fields_dict: Dict[str, Any] = {}
            for f in fields_list:
                if not isinstance(f, dict):
                    continue
                fname = f.get('name')
                ftype = f.get('type')
                fdesc = f.get('description')
                if fname and ftype:
                    fields_dict[fname] = {"type": ftype}
                    if fdesc is not None:
                        fields_dict[fname]["description"] = fdesc
            lib[name] = {"type": "model", "fields": fields_dict}
    return lib


def _normalize_registry_map(registry: Any) -> Dict[str, Any]:
    """Normalize registry to dict form."""
    if isinstance(registry, dict):
        return dict(registry)

    reg: Dict[str, Any] = {}
    if isinstance(registry, list):
        for entry in registry:
            if not isinstance(entry, dict):
                continue
            agent = entry.get('agent')
            model = entry.get('agent_definition', None)
            if agent:
                reg[agent] = model
    return reg


def _extract_agent_names(agents_output: Dict[str, Any]) -> List[str]:
    """Return agent variable names from AgentsAgent output."""
    names: List[str] = []
    if isinstance(agents_output, dict) and isinstance(agents_output.get('agents'), list):
        for a in agents_output['agents']:
            if isinstance(a, dict):
                name = a.get('name')
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
    return names


def _merge_structured_outputs(
    static_so: Dict[str, Any],
    dynamic_so: Dict[str, Any],
    agent_names: List[str],
    wf_logger
) -> Dict[str, Any]:
    """Merge static and dynamic structured outputs."""
    static_models = _normalize_model_library(static_so.get('models', {}))
    static_registry = _normalize_registry_map(static_so.get('registry', {}))

    dynamic_models = _normalize_model_library(dynamic_so.get('models', []))
    dynamic_registry = _normalize_registry_map(dynamic_so.get('registry', []))

    merged_models = dict(static_models)
    for mname, mdef in dynamic_models.items():
        merged_models[mname] = mdef

    merged_registry = dict(static_registry)
    for agent, model in dynamic_registry.items():
        merged_registry[agent] = model

    for agent in agent_names:
        if agent not in merged_registry:
            merged_registry[agent] = None

    wf_logger.info(
        f"🧩 [STRUCTURED_OUTPUTS] models={len(merged_models)} registry={len(merged_registry)}"
    )

    return {"models": merged_models, "registry": merged_registry}


def _extract_workflow_strategy_payload(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    payload = raw.get("WorkflowStrategy") or raw.get("workflow_strategy") or raw
    if isinstance(payload, dict):
        return payload
    return {}


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


def _normalize_visual_agents(value: Any, *, startup_mode: Optional[str]) -> Optional[List[str]]:
    mode = str(startup_mode or "").strip().lower()
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


def _normalize_handoff_rules(raw_rules: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_rules, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for rule in raw_rules:
        if not isinstance(rule, dict):
            continue
        item = dict(rule)
        item["condition"] = _normalize_nullable_text(item.get("condition"))
        item["condition_scope"] = _normalize_nullable_text(item.get("condition_scope"))
        item["condition_type"] = _normalize_nullable_text(item.get("condition_type"))

        handoff_type = str(item.get("handoff_type") or "").strip().lower()
        if item.get("condition") is None and handoff_type == "condition":
            item["handoff_type"] = "after_work"
        elif item.get("condition") is not None and not handoff_type:
            item["handoff_type"] = "condition"

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


def _slugify_identifier(value: Optional[str], default: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return default
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    return slug or default


def _build_workflow_local_pack_graph(
    workflow_name: str,
    workflow_strategy_output: Any,
    wf_logger,
) -> Optional[Dict[str, Any]]:
    strategy = _extract_workflow_strategy_payload(workflow_strategy_output)
    decomposition = strategy.get("decomposition")

    if not isinstance(decomposition, dict) or not decomposition.get("required"):
        return None

    if decomposition.get("mode") != "single_stage_mfj":
        wf_logger.info(
            f"⏭️ [CREATE_WORKFLOW_FILES] Skipping workflow-local graph for {workflow_name}: "
            f"unsupported decomposition mode={decomposition.get('mode')}"
        )
        return None

    decomposition_agent = decomposition.get("decomposition_agent")
    child_initial_agent = decomposition.get("child_initial_agent")
    resume_agent = decomposition.get("resume_agent")
    inject_as = decomposition.get("inject_as")

    required_names = {
        "decomposition_agent": decomposition_agent,
        "child_initial_agent": child_initial_agent,
        "resume_agent": resume_agent,
    }
    missing = [name for name, value in required_names.items() if not isinstance(value, str) or not value.strip()]
    if missing:
        wf_logger.warning(
            f"⚠️ [CREATE_WORKFLOW_FILES] Cannot build workflow-local graph for {workflow_name}; "
            f"missing decomposition fields: {', '.join(missing)}"
        )
        return None

    fan_out: Dict[str, Any] = {
        "spawn_mode": "workflow",
        "child_initial_agent": child_initial_agent.strip(),
    }
    max_children = decomposition.get("max_children")
    if isinstance(max_children, int) and max_children > 0:
        fan_out["max_children"] = max_children

    fan_in: Dict[str, Any] = {
        "resume_agent": resume_agent.strip(),
    }
    if isinstance(inject_as, str) and inject_as.strip():
        inject_key = inject_as.strip()
        if not inject_key.startswith("mfj_"):
            inject_key = f"mfj_{_slugify_identifier(inject_key, 'results')}"
        fan_in["inject_as"] = inject_key

    work_unit = decomposition.get("work_unit")
    journey_id = f"{_slugify_identifier(work_unit, 'decomposition')}_cycle"
    journey: Dict[str, Any] = {
        "id": journey_id,
        "decomposition_agent": decomposition_agent.strip(),
        "fan_out": fan_out,
        "fan_in": fan_in,
    }

    graph = {
        "version": 3,
        "mid_flight_journeys": [journey],
    }
    wf_logger.info(
        f"🧩 [CREATE_WORKFLOW_FILES] Built workflow-local graph for {workflow_name} "
        f"(decomposition={decomposition_agent}, resume={resume_agent})"
    )
    return graph


def _save_modular_workflow(
    workflow_name: str,
    config: Dict[str, Any],
    *,
    workflow_dir: Optional[Path] = None,
) -> bool:
    """Save a workflow config as modular YAML files plus co-located UI assets."""
    try:
        wf_logger = get_workflow_logger(workflow_name=workflow_name)
        
        if workflow_dir is None:
            workflow_dir = _resolve_workflow_output_dir(workflow_name, data=config)
        workflow_dir.mkdir(parents=True, exist_ok=True)
        
        sections = _split_config_into_sections(config)
        saved_files = []

        for section_name, section_data in sections.items():
            filename = WORKFLOW_FILE_MAPPINGS.get(section_name)
            if not filename:
                continue

            # structured_outputs
            if section_name == "structured_outputs":
                if isinstance(section_data, dict):
                    section_data.setdefault("models", {})
                    section_data.setdefault("registry", {})
                    file_path = workflow_dir / filename
                    _save_yaml_file(file_path, section_data)
                    saved_files.append(filename)
                    wf_logger.info(f"📄 [SAVE] structured_outputs saved → {filename}")
                continue

            # context_variables (unwrap wrapper)
            if section_name == "context_variables":
                if isinstance(section_data, dict):
                    plan = section_data.get("ContextVariablesPlan") or section_data
                    has_new = any(k in plan for k in ("database_variables", "environment_variables", "derived_variables"))
                    if has_new:
                        file_path = workflow_dir / filename
                        _save_yaml_file(file_path, plan)
                        saved_files.append(filename)
                        wf_logger.info(
                            f"📄 [SAVE] context_variables saved → {filename} "
                            f"(db={len(plan.get('database_variables', []))}, env={len(plan.get('environment_variables', []))}, derived={len(plan.get('derived_variables', []))})"
                        )
                continue

            if section_data:
                file_path = workflow_dir / filename
                _save_yaml_file(file_path, section_data)
                saved_files.append(filename)
                wf_logger.info(f"📄 [SAVE] {section_name} saved → {filename}")

        # Handle extra files (tools, UI components, lifecycle hooks, etc.)
        extra_files = config.get('extra_files')
        if isinstance(extra_files, list) and extra_files:
            tools_dir = workflow_dir / 'tools'
            tools_dir.mkdir(parents=True, exist_ok=True)
            
            extra_saved = 0
            js_files_saved = 0
            py_files_saved = 0
            
            for item in extra_files:
                if not isinstance(item, dict):
                    continue
                    
                # Support both 'path' and 'filename' fields
                rel_path = item.get('path') or item.get('filename')
                content = item.get('content') or item.get('filecontent')
                
                if not rel_path or content is None:
                    continue
                    
                rel_path = _normalize_workflow_extra_path(rel_path)
                if not rel_path:
                    wf_logger.warning("⚠️ [SAVE] Skipping non-local workflow file path")
                    continue
                
                # Route files based on path prefix
                normalized_content = _normalize_file_content(rel_path, content, item.get('agent'))

                # Files inside workflow-local ui/ are frontend surfaces co-located
                # with the workflow pack. Everything else remains backend/runtime data.
                file_path = workflow_dir / rel_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(normalized_content, bytes):
                    file_path.write_bytes(normalized_content)
                else:
                    file_path.write_text(str(normalized_content), encoding='utf-8')
                if rel_path.startswith('ui/'):
                    js_files_saved += 1
                    wf_logger.info(f"📄 [SAVE] Workflow UI file saved → {rel_path}")
                else:
                    py_files_saved += 1
                    wf_logger.info(f"📄 [SAVE] Workflow file saved → {rel_path}")
                saved_files.append(rel_path)
                
                extra_saved += 1
            
            if js_files_saved > 0:
                wf_logger.info(f"✅ [SAVE] Saved {js_files_saved} workflow UI files to {workflow_name}/ui/")
            if py_files_saved > 0:
                wf_logger.info(f"✅ [SAVE] Saved {py_files_saved} Python files to workflows/{workflow_name}/tools/")
            wf_logger.info(f"✅ [SAVE] Saved {extra_saved} total extra files")

        wf_logger.info(f"✅ [SAVE] Saved {len(saved_files)} total files for workflow={workflow_name}")

        # Generate capability spec and websocket config for discoverability
        _generate_capability_spec(workflow_name, config, workflow_dir)
        _generate_websocket_config(workflow_name, config, workflow_dir)

        return True

    except Exception as e:
        get_workflow_logger(workflow_name=workflow_name).error(
            f"❌ [SAVE] Failed to save workflow {workflow_name}: {e}"
        )
        return False


async def convert_workflow_to_modular(data: Dict[str, Any], context_variables: Optional[Any] = None) -> Dict[str, Any]:
    """Save a workflow configuration as modular YAML files."""
    try:
        workflow_name = data.get('workflow_name', 'Generated_Workflow')
        config_to_save = data.get('config')
        wf_logger = get_workflow_logger(workflow_name=workflow_name)

        if not config_to_save:
            return {"status": "error", "message": "No config provided to save"}

        wf_logger.info(f"💾 [CONVERT] Saving modular config for {workflow_name}")
        workflow_dir = _resolve_workflow_output_dir(
            workflow_name,
            data=data,
            context_variables=context_variables,
        )
        success = _save_modular_workflow(workflow_name, config_to_save, workflow_dir=workflow_dir)

        if success:
            return {
                "status": "success",
                "workflow_name": workflow_name,
                "action": "saved_modular",
                "workflow_dir": str(workflow_dir),
            }
        else:
            return {"status": "error", "message": f"Failed to save {workflow_name}"}

    except Exception as e:
        get_workflow_logger(workflow_name=data.get('workflow_name', 'Generated_Workflow')).error(f"❌ [CONVERT] Error: {e}")
        return {"status": "error", "message": str(e)}


async def create_workflow_files(data: Dict[str, Any], context_variables: Optional[Any] = None) -> Dict[str, Any]:
    """
    Create individual workflow YAML files from agent outputs

    Args:
        data: Contains the various workflow sections from agent outputs
            Expected structure:
            {
                'workflow_name': 'MyWorkflow',
                'orchestrator_output': {...},        # OrchestratorAgent
                'workflow_strategy_output': {...},   # WorkflowStrategyAgent
                'agents_output': {...},              # AgentsAgent
                'handoffs_output': {...},            # HandoffsAgent
                'context_variables_output': {...},   # ContextVariablesAgent
                'hooks_output': {...},               # HookAgent (metadata + optional files)
                'structured_outputs': {...},         # Static base (model library + default registry)
                'structured_outputs_agent_output': {...}, # StructuredOutputsAgent (dynamic)
                'tools_manager_output': {...},       # ToolsManagerAgent (tools + lifecycle_tools manifest)
                'ui_file_generator_output': {...},   # UIFileGenerator (UI tool implementations)
                'agent_tools_file_generator_output': {...}, # AgentToolsFileGenerator (agent tools + lifecycle tools implementations)
                'ui_config': {...},                  # UI config (visual_agents)
                'database_intent_output': {...},     # DatabaseIntentAgent
                'extra_files': [...]                 # Additional arbitrary files
            }
        context_variables: AG2 ContextVariables for sharing state between agents

    Returns:
        Response dictionary with creation status and file paths
    """
    try:
        workflow_name = data.get('workflow_name', 'Generated_Workflow')
        wf_logger = get_workflow_logger(workflow_name=workflow_name)
        wf_logger.info(f"📁 [CREATE_WORKFLOW_FILES] Creating modular JSON files for: {workflow_name}")
        
        # Log incoming data for debugging
        wf_logger.info("=" * 80)
        wf_logger.info("📦 RAW DATA RECEIVED BY workflow_converter:")
        wf_logger.info("=" * 80)
        
        # Save detailed input to file for inspection
        try:
            import json as _json
            from datetime import datetime
            
            converter_logs_dir = get_workflow_converter_logs_dir()
            converter_logs_dir.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            input_file = converter_logs_dir / f"converter_input_{workflow_name}_{timestamp}.json"
            
            with open(input_file, 'w', encoding='utf-8') as f:
                _json.dump(data, f, indent=2, ensure_ascii=False)
            
            wf_logger.info(f"📄 Saved converter input to: {input_file.resolve()}")
        except Exception as e:
            wf_logger.debug(f"Failed to save converter input: {e}")
        
        for key, value in data.items():
            if key == 'workflow_name':
                wf_logger.info(f"   workflow_name: {value}")
            elif isinstance(value, dict):
                wf_logger.info(f"   {key}: {list(value.keys())}")
            elif isinstance(value, list):
                wf_logger.info(f"   {key}: [{len(value)} items]")
            else:
                wf_logger.info(f"   {key}: {type(value).__name__}")
        wf_logger.info("=" * 80)

        # Build the complete config from agent outputs
        config: Dict[str, Any] = {}

        # Extract orchestrator settings from OrchestratorAgent output
        orchestrator_output = data.get('orchestrator_output', {})
        if orchestrator_output:
            normalized_orchestrator = dict(orchestrator_output)
            normalized_orchestrator["initial_message_to_user"] = _normalize_nullable_text(
                normalized_orchestrator.get("initial_message_to_user")
            )
            normalized_orchestrator["initial_message"] = _normalize_nullable_text(
                normalized_orchestrator.get("initial_message")
            )
            if "initial_agent" in normalized_orchestrator:
                normalized_orchestrator["initial_agent"] = _normalize_nullable_text(
                    normalized_orchestrator.get("initial_agent")
                )
            normalized_orchestrator["visual_agents"] = _normalize_visual_agents(
                normalized_orchestrator.get("visual_agents"),
                startup_mode=normalized_orchestrator.get("startup_mode"),
            )
            config.update(normalized_orchestrator)
            wf_logger.info(f"📋 [CREATE_WORKFLOW_FILES] Added orchestrator config: {list(normalized_orchestrator.keys())}")
        else:
            wf_logger.warning("⚠️ [CREATE_WORKFLOW_FILES] No orchestrator_output provided")

        # Extract agents from AgentsAgent output.
        # Auto-tool execution is derived later by the runtime from tools.yaml.
        # Transform agents list into dict keyed by agent name for agents.yaml format.
        agents_output = data.get('agents_output', {})
        agent_names = _extract_agent_names(agents_output)
        if agents_output and 'agents' in agents_output:
            agents_list = agents_output['agents']
            
            # Transform list to dict: [{"name": "Agent1", ...}] -> {"Agent1": {...}}
            # Exclude fields that don't belong in agents.yaml runtime config
            excluded_fields = {'name', 'display_name'}
            agents_dict = {}
            
            for agent in agents_list:
                if isinstance(agent, dict) and 'name' in agent:
                    agent_name = agent['name']
                    # Remove excluded fields (name is the key, display_name is metadata)
                    agent_config = {k: v for k, v in agent.items() if k not in excluded_fields}
                    agents_dict[agent_name] = agent_config
                else:
                    wf_logger.warning(f"⚠️ [CREATE_WORKFLOW_FILES] Skipping malformed agent entry: {agent}")
            
            config['agents'] = agents_dict
            wf_logger.info(f"📃 [CREATE_WORKFLOW_FILES] Added {len(agents_dict)} agents (auto-tool execution derived from tools.yaml)")
            wf_logger.info(f"   Agent names: {list(agents_dict.keys())}")
        else:
            wf_logger.warning("⚠️ [CREATE_WORKFLOW_FILES] No agents in agents_output")

        # Extract handoffs from HandoffsAgent output
        handoffs_output = data.get('handoffs_output', {})
        if handoffs_output and 'handoff_rules' in handoffs_output:
            normalized_rules = _normalize_handoff_rules(handoffs_output.get('handoff_rules'))
            config['handoffs'] = {'handoff_rules': normalized_rules}
            wf_logger.info(f"📋 [CREATE_WORKFLOW_FILES] Added {len(normalized_rules)} handoff rules")

        # Extract hooks from HookAgent output (metadata only + optional filecontent -> extra_files)
        hooks_output = data.get('hooks_output', {})
        if hooks_output and 'hooks' in hooks_output:
            raw_hooks = hooks_output.get('hooks', [])
            metadata_hooks: List[Dict[str, Any]] = []
            hook_extra_files: List[Dict[str, Any]] = []
            for h in raw_hooks:
                if not isinstance(h, dict):
                    continue
                filename = h.get('filename') or h.get('file')  # prefer 'filename'
                fn = h.get('function')
                # Normalize function (strip module prefix if present)
                if isinstance(fn, str):
                    if ':' in fn:
                        fn = fn.split(':', 1)[1]
                    if '.' in fn:
                        fn = fn.split('.')[-1]
                    fn = fn.strip()
                metadata_hooks.append({
                    'hook_type': h.get('hook_type'),
                    'hook_agent': h.get('hook_agent'),
                    'filename': filename,
                    'function': fn,
                })
                # Prepare file write (tools/<filename>) if filecontent provided
                filecontent = h.get('filecontent')
                if filename and isinstance(filecontent, str) and filecontent.strip():
                    hook_extra_files.append({
                        'filename': f"tools/{filename}",
                        'filecontent': filecontent
                    })
            if metadata_hooks:
                config['hooks'] = {'hooks': metadata_hooks}
                wf_logger.info(f"📋 [CREATE_WORKFLOW_FILES] Added {len(metadata_hooks)} hook metadata entries")
            if hook_extra_files:
                existing_extra = data.get('extra_files') or []
                if not isinstance(existing_extra, list):
                    existing_extra = []
                existing_extra.extend(hook_extra_files)
                data['extra_files'] = existing_extra
                wf_logger.info(f"🧩 [CREATE_WORKFLOW_FILES] Collected {len(hook_extra_files)} hook implementation files")

        # Extract context variables from ContextVariablesAgent output
        context_variables_output = data.get('context_variables_output', {})
        if context_variables_output and 'context_variables' in context_variables_output:
            config['context_variables'] = context_variables_output
            wf_logger.info(f"📋 [CREATE_WORKFLOW_FILES] Added {len(context_variables_output['context_variables'])} context variables")

        # Extract tools configuration from ToolsManagerAgent
        tools_manager_output = data.get('tools_manager_output', {})

        # -----------------------------
        # Structured outputs (MERGE)
        # -----------------------------
        static_structured = data.get('structured_outputs', {}) or {}
        dynamic_structured = data.get('structured_outputs_agent_output', {}) or {}

        # Normalize dynamic structured outputs: agent may return JSON as string or
        # wrapped under alternative keys. Accept raw dicts or JSON strings.
        def _normalize_dynamic_structured(obj):
            if isinstance(obj, str):
                try:
                    parsed = json.loads(obj)
                    obj = parsed
                except Exception as e:
                    wf_logger.error(f"❌ [STRUCTURED_OUTPUTS] Could not parse structured_outputs_agent_output JSON: {e}")
                    return {}
            if not isinstance(obj, dict):
                return {}

            # If it's wrapped under a top-level key, try to find inner dict containing models/registry
            if 'models' in obj or 'registry' in obj:
                return obj

            # Common wrapper keys used in examples: StructuredOutputsRegistry, StructuredModelsOutput, StructuredOutputs
            for key in ('StructuredOutputsRegistry', 'StructuredModelsOutput', 'StructuredOutputs', 'StructuredOutputsAgent'):
                if key in obj and isinstance(obj[key], dict):
                    return obj[key]

            # Fallback: search nested dict values for models/registry keys
            for v in obj.values():
                if isinstance(v, dict) and ('models' in v or 'registry' in v):
                    return v

            return {}

        dynamic_structured = _normalize_dynamic_structured(dynamic_structured)

        merged_structured = _merge_structured_outputs(static_structured, dynamic_structured, agent_names, wf_logger)

        # Guarantee presence of top-level keys even if empty
        if not isinstance(merged_structured.get('models'), dict):
            merged_structured['models'] = {}
        if not isinstance(merged_structured.get('registry'), dict):
            merged_structured['registry'] = {}

        config['structured_outputs'] = merged_structured
        wf_logger.info("📋 [CREATE_WORKFLOW_FILES] Prepared structured_outputs (merged static+dynamic and completed registry)")

        # Add tools configuration from ToolsManagerAgent (authoritative)
        # Note: agent flags are extracted separately above and merged into agents.yaml
        if isinstance(tools_manager_output, dict):
            if "tools" in tools_manager_output or "lifecycle_tools" in tools_manager_output:
                tools_config = _normalize_tools_manifest(tools_manager_output, wf_logger)
                if tools_config.get("tools") is not None:
                    wf_logger.info(
                        "📋 [CREATE_WORKFLOW_FILES] Added tools configuration (tools_list=%d)",
                        len(tools_config.get("tools") or []),
                    )
                if tools_config.get("lifecycle_tools") is not None:
                    wf_logger.info(
                        "📋 [CREATE_WORKFLOW_FILES] Added lifecycle_tools configuration (lifecycle_tools_list=%d)",
                        len(tools_config.get("lifecycle_tools") or []),
                    )
                if tools_config:
                    config["tools"] = tools_config
        # Add UI configuration
        ui_config = data.get('ui_config', {})
        if ui_config:
            normalized_ui_config = dict(ui_config)
            normalized_ui_config['visual_agents'] = _normalize_visual_agents(
                normalized_ui_config.get('visual_agents'),
                startup_mode=config.get('startup_mode'),
            )
            config.update(normalized_ui_config)
            wf_logger.info("📋 [CREATE_WORKFLOW_FILES] Added UI configuration")

        # Add extra files to config so they can be saved
        extra_files = data.get('extra_files')

        # ------------------------------------------------------------------
        # Database intent bundle (db_intent.json) for downstream AppGenerator
        # ------------------------------------------------------------------
        database_intent_output = data.get("database_intent_output")
        db_intent_content: Dict[str, Any] = {}

        try:
            if isinstance(database_intent_output, dict) and database_intent_output:
                payload = database_intent_output.get("DatabaseIntent")
                if not isinstance(payload, dict):
                    payload = database_intent_output

                if isinstance(payload, dict):
                    db_intent_content = payload
        except Exception as intent_extract_err:
            wf_logger.debug(f"Failed to extract database intent output: {intent_extract_err}")

        if not isinstance(extra_files, list):
            extra_files = []

        def _extra_has(target_name: str) -> bool:
            for item in extra_files:
                if not isinstance(item, dict):
                    continue
                if item.get("filename") == target_name or item.get("path") == target_name:
                    return True
            return False

        workflow_strategy_output = data.get("workflow_strategy_output", {})
        workflow_local_graph = _build_workflow_local_pack_graph(
            workflow_name=workflow_name,
            workflow_strategy_output=workflow_strategy_output,
            wf_logger=wf_logger,
        )
        if workflow_local_graph and not _extra_has("extended_orchestration/mfj_extension.json"):
            extra_files.append(
                {
                    "filename": "extended_orchestration/mfj_extension.json",
                    "filecontent": workflow_local_graph,
                    "agent": "WorkflowStrategyAgent",
                }
            )
            wf_logger.info("🧩 [CREATE_WORKFLOW_FILES] Added workflow-local extended_orchestration/mfj_extension.json to extra_files")

        if isinstance(db_intent_content, dict) and db_intent_content:
            if not _extra_has("db_intent.json"):
                extra_files.append(
                    {"filename": "db_intent.json", "filecontent": db_intent_content, "agent": "DatabaseIntentAgent"}
                )
            wf_logger.info("🧩 [CREATE_WORKFLOW_FILES] Added db_intent.json to extra_files")

        # Inject runtime-generated attachments (e.g., API key env snapshot) from context variables
        if context_variables and hasattr(context_variables, 'get'):
            try:
                env_attachment = context_variables.get('api_keys_env_attachment')
            except Exception:
                env_attachment = None
            if isinstance(env_attachment, dict):
                env_filename = (env_attachment.get('filename') or 'api_keys.env').strip()
                env_content = env_attachment.get('filecontent')
                if env_content is None:
                    env_content = env_attachment.get('content')
                if env_filename and env_content is not None:
                    if not isinstance(extra_files, list):
                        extra_files = []
                    already_present = any(
                        isinstance(item, dict) and item.get('filename') == env_filename
                        for item in extra_files
                    )
                    if not already_present:
                        extra_files.append({'filename': env_filename, 'filecontent': env_content})
                        wf_logger.info("🧩 [CREATE_WORKFLOW_FILES] Added API key env attachment to extra_files")

        # Extract files from UIFileGenerator output (CodeFile objects)
        ui_file_generator_output = data.get('ui_file_generator_output', {})
        if ui_file_generator_output:
            ui_tool_files = _collect_ui_code_files(
                ui_file_generator_output,
                tools_config=config.get("tools"),
                wf_logger=wf_logger,
            )
            if ui_tool_files:
                if extra_files:
                    extra_files.extend(ui_tool_files)
                else:
                    extra_files = ui_tool_files
                wf_logger.info(
                    "📋 [CREATE_WORKFLOW_FILES] Added %d UI files from UIFileGenerator",
                    len(ui_tool_files),
                )

        # Extract files from AgentToolsFileGenerator output
        agent_tools_output = data.get('agent_tools_file_generator_output', {})
        if agent_tools_output:
            agent_tool_files = _collect_code_files(
                agent_tools_output,
                source_name="AgentToolsFileGenerator",
                wf_logger=wf_logger,
            )
            if agent_tool_files:
                if extra_files:
                    extra_files.extend(agent_tool_files)
                else:
                    extra_files = agent_tool_files
                wf_logger.info(
                    "📋 [CREATE_WORKFLOW_FILES] Added %d backend tool files from AgentToolsFileGenerator",
                    len(agent_tool_files),
                )

        if isinstance(extra_files, list) and extra_files:
            config['extra_files'] = extra_files
            wf_logger.info(f"📋 [CREATE_WORKFLOW_FILES] Total extra files to save: {len(extra_files)}")

        # Apply backend defaults for orchestrator fields if missing
        def _apply_orchestrator_defaults(cfg: Dict[str, Any]):
            # Core defaults
            cfg.setdefault('max_turns', 25)
            cfg.setdefault('human_in_the_loop', False)
            cfg.setdefault('orchestration_pattern', 'DefaultPattern')
            cfg.setdefault('startup_mode', 'BackendOnly')
            cfg['initial_message_to_user'] = _normalize_nullable_text(cfg.get('initial_message_to_user'))
            cfg['initial_message'] = _normalize_nullable_text(cfg.get('initial_message'))
            cfg['initial_agent'] = _normalize_nullable_text(cfg.get('initial_agent'))
            # Message logic
            raw_mode = str(cfg.get('startup_mode') or '').strip()
            mode_map = {
                'userdriven': 'UserDriven',
                'agentdriven': 'AgentDriven',
                'backendonly': 'BackendOnly',
            }
            mode = mode_map.get(raw_mode.lower(), raw_mode or 'BackendOnly')
            cfg['startup_mode'] = mode
            if mode == 'UserDriven':
                if 'initial_message_to_user' not in cfg or cfg.get('initial_message_to_user') is None:
                    cfg['initial_message_to_user'] = 'Please provide the required input to begin.'
                cfg['initial_message'] = None
            else:
                if 'initial_message' not in cfg or cfg.get('initial_message') is None:
                    cfg['initial_message'] = 'Initialize workflow sequence.'
                cfg['initial_message_to_user'] = None
            # Extensions default
            cfg['runtime_extensions'] = _normalize_runtime_extensions(
                cfg.get('runtime_extensions'),
                workflow_name=workflow_name,
                wf_logger=wf_logger,
            )
            cfg['triggers'] = _normalize_orchestrator_triggers(
                cfg.get('triggers'),
                wf_logger=wf_logger,
            )
            cfg['visual_agents'] = _normalize_visual_agents(
                cfg.get('visual_agents'),
                startup_mode=mode,
            )
            if cfg.get('initial_agent') is None and isinstance(cfg.get('agents'), dict):
                for agent_name in cfg['agents'].keys():
                    if isinstance(agent_name, str) and agent_name.strip():
                        cfg['initial_agent'] = agent_name
                        break
            return cfg

        _apply_orchestrator_defaults(config)

        # Save as modular JSON files using self-contained function
        workflow_dir = _resolve_workflow_output_dir(
            workflow_name,
            data=data,
            context_variables=context_variables,
        )
        success = _save_modular_workflow(workflow_name, config, workflow_dir=workflow_dir)

        if success:
            created_files = []

            for section, filename in WORKFLOW_FILE_MAPPINGS.items():
                file_path = workflow_dir / filename
                if file_path.exists():
                    created_files.append(filename)

            # Include extra files saved
            try:
                if 'extra_files' in config:
                    for item in config['extra_files']:
                        if isinstance(item, dict):
                            safe_name = _normalize_workflow_extra_path(item.get('filename') or item.get('path'))
                            if safe_name and (workflow_dir / safe_name).exists():
                                created_files.append(safe_name)
            except Exception:
                pass

            wf_logger.info(f"✅ [CREATE_WORKFLOW_FILES] Created {len(created_files)} modular JSON files for: {workflow_name}")

            # Update context variables to track created workflow
            if context_variables:
                workflow_files = context_variables.get('generated_workflow_files', [])
                if workflow_files is None:
                    workflow_files = []

                workflow_record = {
                    'workflow_name': workflow_name,
                    'files': created_files,
                    'file_count': len(created_files),
                    'workflow_dir': str(workflow_dir),
                    'created_at': str(__import__('time').time())
                }

                workflow_files.append(workflow_record)
                context_variables.set('generated_workflow_files', workflow_files)
                context_variables.set('latest_workflow', workflow_record)

                wf_logger.info("📝 [CREATE_WORKFLOW_FILES] Updated context variables with workflow record")

            return {
                "status": "success",
                "message": f"Successfully created {len(created_files)} modular JSON files for workflow '{workflow_name}'",
                "workflow_name": workflow_name,
                "files": created_files,
                "file_count": len(created_files),
                "workflow_dir": str(workflow_dir),
                "workflow_config": config,
                "details": f"Created: {', '.join(created_files)}"
            }
        else:
            return {
                "status": "error",
                "message": f"Failed to create modular JSON files for workflow '{workflow_name}'"
            }

    except Exception as e:
        get_workflow_logger(workflow_name=data.get('workflow_name', 'Generated_Workflow')).error(f"❌ [CREATE_WORKFLOW_FILES] Error: {e}")
        return {"status": "error", "message": str(e)}


# Export the main functions for use in the workflow
__all__ = ['convert_workflow_to_modular', 'create_workflow_files', 'promote_generated_workflow']
