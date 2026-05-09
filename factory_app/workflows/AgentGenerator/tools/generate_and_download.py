# ==============================================================================
# FILE: workflows\Generator\tools\generate_and_download.py
# DESCRIPTION: Generate and download workflow files - async UI tool with runtime-derived auto-tool execution
# NOTE: 'generate_and_download' function adheres to AGENT CONTRACT for tool integration. 
# ==============================================================================

from typing import Any, Dict, List, Optional, Union, Annotated
import os
import json
import uuid
from pathlib import Path
import shutil
import time
import zipfile
from logs.logging_config import get_workflow_logger
from logs.runtime_artifacts import get_agent_outputs_dir
from mozaiksai.core.chat_attachments import inject_bundle_attachments_into_payload
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool
from mozaiksai.core.workflow.generator_support.agent_endpoints import (
    resolve_agent_api_url,
    resolve_agent_websocket_url,
)
from mozaiksai.core.workflow.generator_support.workflow_artifacts import record_workflow_artifacts
from mozaiksai.core.workflow.generator_support.workflow_exports import record_workflow_export
from .export_agent_workflow import export_agent_workflow_to_github
from .workflow_converter import create_workflow_files, promote_generated_workflow

try:
    from logs.tools_logs import get_tool_logger as _get_tool_logger, log_tool_event as _log_tool_event  # type: ignore
except Exception:
    _get_tool_logger = None  # type: ignore
    _log_tool_event = None  # type: ignore

import logging as _logging
_promote_logger = _logging.getLogger(__name__)


def _promote_workflow_to_app_workspace(workflow_dir: Path, wf_name: str) -> None:
    """
    Copy the generated workflow into the active app workspace's workflows/ directory.

    Reads MOZAIKS_APP_WORKSPACE_PATH to locate the target. No-op when the env var
    is unset (OSS / CI / local dev contexts without a configured workspace).

    Logs a warning on failure but never raises — promotion is best-effort and must
    not abort the download that already succeeded.
    """
    workspace = os.getenv("MOZAIKS_APP_WORKSPACE_PATH", "").strip()
    if not workspace:
        _promote_logger.debug("[%s] MOZAIKS_APP_WORKSPACE_PATH not set — skipping workflow promotion", wf_name)
        return

    target_root = Path(workspace) / "workflows"
    try:
        result = promote_generated_workflow(workflow_dir, target_root)
        _promote_logger.info(
            "[%s] Promoted generated workflow to app workspace: %s",
            wf_name,
            result["target_dir"],
        )
    except Exception as exc:
        _promote_logger.warning(
            "[%s] Failed to promote generated workflow to app workspace (%s): %s",
            wf_name,
            target_root,
            exc,
        )


async def _register_workflow_bundle_artifact_version(
    *,
    app_id: str,
    user_id: Optional[str],
    workflow_name: str,
    chat_id: Optional[str],
    bundle_name: str,
    zip_path: Optional[Path],
    context_variables: Optional[Any],
):
    try:
        import hashlib
        from mozaiksai.core.artifacts import (
            ArtifactLifecycleStatus,
            ArtifactValidationStatus,
            get_artifact_store,
            resolve_latest_artifact_version_refs,
        )
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Artifact store dependencies unavailable: {exc}") from exc

    files_manifest = []
    if zip_path is not None and zip_path.exists():
        raw = zip_path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        files_manifest.append(
            {
                "path": f"{bundle_name}/{zip_path.name}",
                "sha256": sha,
                "size_bytes": zip_path.stat().st_size,
                "content_type": "application/zip",
            }
        )

    parent_version_id = None
    if context_variables and hasattr(context_variables, "get"):
        try:
            parent_version_id = context_variables.get("artifact_version_id")
        except Exception:
            parent_version_id = None

    artifact_store = get_artifact_store()
    canonical_inputs_version = await resolve_latest_artifact_version_refs(
        app_id=str(app_id),
        artifact_kinds=("concept", "build_plan", "design_docs"),
        artifact_store=artifact_store,
    )
    artifact_version = await artifact_store.create_artifact_version(
        app_id=str(app_id),
        artifact_kind="workflow_bundle",
        artifact_key=bundle_name,
        parent_version_id=str(parent_version_id) if parent_version_id else None,
        canonical_inputs_version=canonical_inputs_version,
        files_manifest=files_manifest,
        source_workflow=workflow_name,
        source_chat_id=chat_id,
        lifecycle_status=(
            ArtifactLifecycleStatus.DRAFT
            if parent_version_id
            else ArtifactLifecycleStatus.CURRENT
        ),
        validation_status=ArtifactValidationStatus.PENDING,
        commit_metadata={
            "message": f"{workflow_name}: {bundle_name}",
            "author_user_id": user_id,
            "source_workflow": workflow_name,
            "source_chat_id": chat_id,
            "metadata": {
                "artifact_path": str(zip_path) if zip_path else None,
                "workflow_name": bundle_name,
            },
        },
    )
    if context_variables and hasattr(context_variables, "set"):
        try:
            context_variables.set("artifact_version_id", artifact_version.id)
        except Exception:
            pass
    return artifact_version


async def generate_and_download(
    DownloadRequest: Annotated[Dict[str, Any], "Download configuration with confirmation_only and storage_backend"],
    agent_message: Annotated[str, "Concise message (≤140 chars) shown to user in download UI"],
    context_variables: Annotated[Optional[Any], "Injected runtime context."] = None,
) -> Dict[str, Any]:
    """
    Generate workflow files and present a download UI to the user.
    
    This tool is automatically invoked when DownloadAgent emits a DownloadRequestCall structured output.
    The runtime extracts DownloadRequest and agent_message from the agent's JSON emission.

    Two operation modes (controlled by DownloadRequest.confirmation_only):
      - confirmation_only=true: Show confirmation UI first, create files after user confirms (two-step).
      - confirmation_only=false: Create files immediately, then show download UI (one-step).

    Args:
        DownloadRequest: Dict with keys:
            - confirmation_only (bool): If true, ask user before creating files.
            - storage_backend (str): Storage target ('none', 's3', 'local').
            - description (str|None): Optional description (reserved for future use).
        agent_message: Message shown to user in UI (e.g., "Ready to download your workflow bundle?").
        context_variables: Runtime context dict (chat_id, workflow_name, user_id, app_id).

    Returns:
        Dict with status, ui_response, files, and any storage metadata.
    """
    # Extract parameters from DownloadRequest structured output
    confirmation_only = DownloadRequest.get("confirmation_only", True)
    storage_backend = DownloadRequest.get("storage_backend", "none")
    description = DownloadRequest.get("description")
    
    # Extract parameters from AG2 ContextVariables
    chat_id: Optional[str] = None
    app_id: Optional[str] = None
    workflow_name: Optional[str] = None
    user_id: Optional[str] = None
    
    if context_variables and hasattr(context_variables, 'get'):
        chat_id = context_variables.get('chat_id')
        app_id = context_variables.get('app_id')
        workflow_name = context_variables.get('workflow_name')
        user_id = context_variables.get('user_id')

    wf_logger = get_workflow_logger(workflow_name=(workflow_name or "missing"), chat_id=chat_id, app_id=app_id)
    tlog = None
    if _get_tool_logger:
        try:
            tlog = _get_tool_logger(tool_name="GenerateAndDownload", chat_id=chat_id, app_id=app_id, workflow_name=(workflow_name or "missing"))
            if _log_tool_event:
                _log_tool_event(tlog, action="start", status="ok")
        except Exception:
            tlog = None
    wf_logger.info(f"🏗️ Starting generate_and_download (confirmation_only={confirmation_only}) chat: {chat_id}")

    agent_message_text = agent_message or description or (
        "Would you like to download the generated workflow bundle now?"
        if confirmation_only else
        "Preparing your workflow files. Use the download panel when ready."
    )
    agent_message_id = f"msg_{uuid.uuid4().hex[:10]}"
    print(agent_message_text)

    if not chat_id or not app_id:
        return {"status": "error", "message": "chat_id and app_id are required"}

    # PHASE 1: Gather latest agent JSON outputs (always needed for metadata or file creation)
    pm = AG2PersistenceManager()
    wf_logger.info(f"🔍 [GATHER] Calling gather_latest_agent_jsons for chat_id={chat_id} app_id={app_id}")
    collected = await pm.gather_latest_agent_jsons(chat_id=chat_id, app_id=app_id)
    wf_logger.info(f"🔍 [GATHER] Collected {len(collected)} agent outputs: {list(collected.keys())}")

    # ------------------------------------------------------------------
    # (Attachments injection is handled via core.artifacts.attachments helpers)
    # ------------------------------------------------------------------
    
    if not collected:
        wf_logger.error(f"❌ [GATHER] No agent outputs collected from persistence! This means either:")
        wf_logger.error(f"   1. Workflow status is not IN_PROGRESS")
        wf_logger.error(f"   2. No messages were persisted to MongoDB")
        wf_logger.error(f"   3. Messages don't have role='assistant' or agent_name field")
        wf_logger.error(f"   4. Message content doesn't contain valid JSON")
        wf_logger.error(f"   Check persistence_manager.py logs for [RESUME_CHAT] and [GATHER_AGENT_JSONS] details")
    
    # Save agent outputs to dedicated debug file
    try:
        from datetime import datetime
        debug_dir = get_agent_outputs_dir()
        debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_file = debug_dir / f"agent_outputs_{chat_id}_{timestamp}.json"
        
        debug_data = {
            "chat_id": chat_id,
            "app_id": app_id,
            "workflow_name": workflow_name,
            "timestamp": timestamp,
            "collected_agents": list(collected.keys()),
            "agent_outputs": {}
        }
        
        for agent_name, output_data in collected.items():
            debug_data["agent_outputs"][agent_name] = {
                "type": type(output_data).__name__,
                "data": output_data if isinstance(output_data, (dict, list, str, int, float, bool, type(None))) else str(output_data)
            }
        
        with open(debug_file, 'w', encoding='utf-8') as f:
            json.dump(debug_data, f, indent=2, ensure_ascii=False)
        
        abs_path = debug_file.resolve()
        wf_logger.info(f"📄 Agent outputs saved to: {abs_path}")
        print("\n" + "=" * 80)
        print(f"📄 AGENT OUTPUTS DEBUG FILE CREATED:")
        print(f"   {abs_path}")
        print("=" * 80 + "\n")
    except Exception as e:
        wf_logger.error(f"❌ Failed to save agent outputs debug file: {e}", exc_info=True)
        print(f"\n⚠️ Warning: Could not save agent outputs debug file: {e}\n")
    
    # Log summary to console
    wf_logger.info("=" * 80)
    wf_logger.info("📋 AGENT OUTPUTS COLLECTED FROM CHAT MESSAGES:")
    wf_logger.info("=" * 80)
    for agent_name, output_data in collected.items():
        wf_logger.info(f"🤖 {agent_name}:")
        if isinstance(output_data, dict):
            wf_logger.info(f"   Keys: {list(output_data.keys())}")
        else:
            wf_logger.info(f"   Type: {type(output_data).__name__}")
    wf_logger.info("=" * 80)
    
    # Extract workflow name from context_variables.action_plan or WorkflowStrategyAgent output
    # Then convert to PascalCase for folder/zip naming
    def _to_pascal_case(name: str) -> str:
        """Convert user-friendly name to PascalCase (e.g., 'Story Creator' -> 'StoryCreator')
        
        If already PascalCase with no spaces (e.g., 'ContentMarketingAutomation'), return as-is.
        If has spaces/hyphens/underscores, convert each word's first letter to uppercase.
        """
        if not name:
            return name
        
        # If no spaces/hyphens/underscores and starts with uppercase, assume already PascalCase
        if ' ' not in name and '-' not in name and '_' not in name and name[0].isupper():
            return name
        
        # Otherwise, split on delimiters and capitalize first letter of each word
        words = name.replace('_', ' ').replace('-', ' ').split()
        # Use title() to capitalize first letter while preserving internal capitals
        return ''.join(word.title() for word in words if word)
    
    wf_name_user_friendly = None
    wf_name_pascal = None
    
    # PRIORITY 1: Try to extract from context_variables.action_plan (for auto-tool agents)
    if context_variables and hasattr(context_variables, 'get'):
        action_plan_ctx = context_variables.get("action_plan")
        if isinstance(action_plan_ctx, dict):
            if isinstance(action_plan_ctx.get("workflow"), dict):
                workflow_info = action_plan_ctx.get("workflow") or {}
            else:
                workflow_info = action_plan_ctx

            if isinstance(workflow_info, dict):
                candidate_name = workflow_info.get("name")
                if isinstance(candidate_name, str) and candidate_name.strip():
                    wf_name_user_friendly = candidate_name.strip()
                    wf_name_pascal = _to_pascal_case(wf_name_user_friendly)
                    wf_logger.info(f"📝 Extracted workflow name from context.action_plan: '{wf_name_user_friendly}' → '{wf_name_pascal}'")
    
    # PRIORITY 2: Try to extract from collected agent JSONs (fallback for non-auto-tool agents)
    if not wf_name_pascal:
        strategy_data = collected.get("WorkflowStrategyAgent")
        if isinstance(strategy_data, dict):
            strategy_payload = strategy_data.get("WorkflowStrategy") or strategy_data.get("workflow_strategy") or strategy_data
            if isinstance(strategy_payload, dict):
                candidate_name = strategy_payload.get("workflow_name")
                if isinstance(candidate_name, str) and candidate_name.strip():
                    wf_name_user_friendly = candidate_name.strip()
                    wf_name_pascal = _to_pascal_case(wf_name_user_friendly)
                    wf_logger.info(f"📝 Extracted workflow name from collected.WorkflowStrategyAgent: '{wf_name_user_friendly}' → '{wf_name_pascal}'")
    
    if not wf_name_pascal:
        orchestrator_snapshot = collected.get("OrchestratorAgent")
        if isinstance(orchestrator_snapshot, dict):
            candidate_name = orchestrator_snapshot.get("workflow_name")
            if isinstance(candidate_name, str) and candidate_name.strip():
                wf_name_user_friendly = candidate_name.strip()
                wf_name_pascal = _to_pascal_case(wf_name_user_friendly)
                wf_logger.info(f"📝 Extracted workflow name from collected.OrchestratorAgent: '{wf_name_user_friendly}' → '{wf_name_pascal}'")
    
    # PRIORITY 3: Fallback to orchestrator or context workflow_name
    if not wf_name_pascal:
        fallback_name = collected.get("workflow_name") or workflow_name or "GeneratedWorkflow"
        if isinstance(fallback_name, str) and fallback_name.strip():
            wf_name_pascal = _to_pascal_case(fallback_name.strip())
        wf_logger.info(f"⚠️ No workflow name in ActionPlan, using fallback: '{wf_name_pascal}'")
    
    if not wf_name_pascal:
        return {"status": "error", "message": "workflow_name missing (not provided and not in persistence)"}
    
    # Use PascalCase name for all file operations
    wf_name = wf_name_pascal
    wf_logger = get_workflow_logger(workflow_name=wf_name, chat_id=chat_id, app_id=app_id)

    if context_variables and hasattr(context_variables, "set"):
        try:
            context_variables.set("workflow_name", wf_name)
        except Exception as ctx_err:
            wf_logger.debug(f"Failed to persist workflow_name to context: {ctx_err}")

    # Build aggregation payload (used later if/when we create files)
    # Map all Generator workflow agent outputs according to the structured outputs registry
    payload: Dict[str, Any] = {
        "workflow_name": wf_name,
    }
    
    # Map agent outputs by their structured output names
    # OrchestratorAgent -> OrchestratorAgentOutput
    orchestrator_data = collected.get("OrchestratorAgent") or collected.get("orchestrator_output", {})
    if isinstance(orchestrator_data, dict):
        payload["orchestrator_output"] = orchestrator_data
    
    # AgentsAgent -> AgentsAgentOutput  
    agents_data = collected.get("AgentsAgent") or collected.get("agents_output", {})
    if isinstance(agents_data, dict):
        payload["agents_output"] = agents_data
    
    # HandoffsAgent -> HandoffsAgentOutput
    handoffs_data = collected.get("HandoffsAgent") or collected.get("handoffs_output", {})
    if isinstance(handoffs_data, dict):
        payload["handoffs_output"] = handoffs_data
    
    # ContextVariablesAgent -> ContextVariablesAgentOutput
    context_vars_data = collected.get("ContextVariablesAgent") or collected.get("context_variables_output", {})
    if isinstance(context_vars_data, dict):
        payload["context_variables_output"] = context_vars_data

    # DatabaseIntentAgent -> DatabaseIntentOutput (db_intent.json)
    database_intent_data = collected.get("DatabaseIntentAgent") or collected.get("database_intent_output", {})
    if isinstance(database_intent_data, dict):
        payload["database_intent_output"] = database_intent_data

    # StateArchitectAgent -> StateArchitectureOutput (tenancy + consistency requirements)
    state_architect_data = collected.get("StateArchitectAgent") or collected.get("state_architecture_output", {})
    if isinstance(state_architect_data, dict):
        payload["state_architecture_output"] = state_architect_data
    
    # ToolsManagerAgent -> ToolsManifest (tools + lifecycle_tools manifest)
    tools_manager_data = collected.get("ToolsManagerAgent") or collected.get("tools_manager_output", {})
    if isinstance(tools_manager_data, dict):
        payload["tools_manager_output"] = tools_manager_data

    # WorkflowStrategyAgent -> WorkflowStrategyOutput (workflow stages + decomposition contract)
    workflow_strategy_data = collected.get("WorkflowStrategyAgent") or collected.get("workflow_strategy_output", {})
    if isinstance(workflow_strategy_data, dict):
        payload["workflow_strategy_output"] = workflow_strategy_data
    
    # UIFileGenerator -> UIFileGeneratorOutput (UI tool implementations)
    ui_file_gen_data = collected.get("UIFileGenerator") or collected.get("ui_file_generator_output", {})
    if isinstance(ui_file_gen_data, dict):
        payload["ui_file_generator_output"] = ui_file_gen_data
    
    # AgentToolsFileGenerator -> AgentToolsFileGeneratorOutput (agent tools + lifecycle tools)
    agent_tools_data = collected.get("AgentToolsFileGenerator") or collected.get("agent_tools_file_generator_output", {})
    if isinstance(agent_tools_data, dict):
        payload["agent_tools_file_generator_output"] = agent_tools_data
    
    # HookAgent -> HookAgentOutput (lifecycle hooks metadata + files)
    hooks_data = collected.get("HookAgent") or collected.get("hooks_output", {})
    if isinstance(hooks_data, dict):
        payload["hooks_output"] = hooks_data
    
    # StructuredOutputsAgent -> StructuredOutputsAgentOutput (dynamic models)
    structured_dynamic = collected.get("StructuredOutputsAgent") or collected.get("structured_outputs_agent_output", {})
    if isinstance(structured_dynamic, dict):
        payload["structured_outputs_agent_output"] = structured_dynamic
    
    # Static structured outputs from Generator workflow itself
    static_structured = collected.get("structured_outputs", {})
    if isinstance(static_structured, dict):
        payload["structured_outputs"] = static_structured
    
    # UI Config (visual_agentsarrays)
    ui_config_data = collected.get("ui_config", {})
    if isinstance(ui_config_data, dict):
        payload["ui_config"] = ui_config_data

    # Generic discovery of code_files in any agent output
    def _discover_code_files(col: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for agent_name, data in col.items():
            try:
                if isinstance(data, dict):
                    cf = data.get("code_files")
                    if isinstance(cf, list):
                        for item in cf:
                            if isinstance(item, dict) and item.get("filename") and item.get("content"):
                                out.append({
                                    "filename": str(item["filename"]),
                                    "filecontent": str(item["content"]),
                                    "purpose": f"from {agent_name}",
                                })
                elif isinstance(data, str):
                    try:
                        parsed = json.loads(data)
                        if isinstance(parsed, dict) and isinstance(parsed.get("code_files"), list):
                            for item in parsed.get("code_files", []):
                                if isinstance(item, dict) and item.get("filename") and item.get("content"):
                                    out.append({
                                        "filename": str(item["filename"]),
                                        "filecontent": str(item["content"]),
                                        "purpose": f"from {agent_name}",
                                    })
                    except Exception:
                        pass
            except Exception:
                continue
        return out

    discovered = _discover_code_files(collected)
    if discovered:
        existing = payload.get("extra_files", []) or []
        seen = {f.get("filename") for f in existing if isinstance(f, dict)}
        for item in discovered:
            fn = item.get("filename")
            if fn and fn not in seen:
                existing.append(item)
        if existing:
            payload["extra_files"] = existing

    # Include any user-uploaded files explicitly tagged for bundling
    # Hybrid model: uploads are context-only unless the workflow flag is enabled.
    allow_bundling = False
    try:
        if context_variables and hasattr(context_variables, "get"):
            allow_bundling = bool(context_variables.get("attachments_allow_bundling", False))
    except Exception:
        allow_bundling = False

    if allow_bundling:
        try:
            coll = await pm._coll()
            injected = await inject_bundle_attachments_into_payload(
                chat_coll=coll,
                payload=payload,
                chat_id=chat_id,
                app_id=app_id,
            )
            if injected:
                wf_logger.info(f"🧩 Injected {injected} uploaded bundle attachment(s) into extra_files")
        except Exception as attach_err:
            wf_logger.warning(f"⚠️ Failed to inject uploaded attachments: {attach_err}")
    else:
        wf_logger.info("📎 Attachment bundling disabled (attachments_allow_bundling=false)")

    # Simplified logic: confirmation_only determines when files are created
    create_res: Dict[str, Any] = {}
    ui_files: List[Dict[str, Any]] = []
    workflow_dir: Optional[Path] = None

    def _format_bytes(num: int) -> str:  # local helper
        try:
            value: float = float(num)
            for unit in ['bytes', 'KB', 'MB', 'GB', 'TB']:
                if value < 1024 or unit == 'TB':
                    if unit == 'bytes':
                        return f"{int(value)} bytes"
                    return f"{value:.1f} {unit}"
                value /= 1024.0
        except Exception:
            return f"{num} bytes"
        return f"{num} bytes"

    def _build_zip_ui_files(*, workflow_dir: Path, created_files: List[str]) -> List[Dict[str, Any]]:
        """Create a single ZIP bundle for this workflow and return ui_files entries."""

        zip_path = workflow_dir.parent / f"{wf_name}.zip"
        wf_logger.info(f"📦 Creating zip archive: {zip_path}")

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add all workflow root files
            for file_name in created_files:
                file_path = workflow_dir / file_name
                if file_path.exists():
                    arcname = f"{wf_name}/{file_name}"
                    zipf.write(file_path, arcname=arcname)

            # Add tools directory if it exists
            tools_dir = workflow_dir / "tools"
            if tools_dir.exists() and tools_dir.is_dir():
                for tool_file in tools_dir.rglob("*"):
                    if tool_file.is_file():
                        arcname = f"{wf_name}/tools/{tool_file.relative_to(tools_dir)}"
                        zipf.write(tool_file, arcname=arcname)

        zip_size = zip_path.stat().st_size
        wf_logger.info(f"✅ Created zip archive: {_format_bytes(zip_size)}")

        return [
            {
                "name": f"{wf_name}.zip",
                "size": _format_bytes(zip_size),
                "size_bytes": zip_size,
                "path": str(zip_path.resolve()),
                "id": "file-zip-bundle",
                "type": "zip",
            }
        ]

    def _extract_names(raw: Any, *, list_key: str) -> List[str]:
        if isinstance(raw, dict):
            if list_key == "agents":
                return sorted(
                    [
                        key.strip()
                        for key in raw.keys()
                        if isinstance(key, str) and key.strip()
                    ]
                )
            items: Any = raw.get(list_key)
            if not isinstance(items, list):
                return []

            names: List[str] = []
            for item in items:
                name = None
                if isinstance(item, dict):
                    name = item.get("name") or item.get("function")
                elif isinstance(item, str):
                    name = item
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
            return names
        if isinstance(raw, list):
            names = []
            for item in raw:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("function")
                else:
                    name = item if isinstance(item, str) else None
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
            return names
        return []

    async def _record_agent_generator_context(
        *, workflow_config: Dict[str, Any], generated_workflow_dir: Optional[Path] = None
    ) -> None:
        try:
            if not app_id:
                return

            agent_names = _extract_names(workflow_config.get("agents"), list_key="agents")
            tool_names = _extract_names(workflow_config.get("tools"), list_key="tools")

            websocket_url = resolve_agent_websocket_url(str(app_id))
            api_url = resolve_agent_api_url(str(app_id))

            # Derive capability_id: stable hyphenated ID AppGenerator uses in
            # workflow_capability_ids fields of module.yaml and events.yaml.
            capability_id = (
                wf_name.lower().replace("_", "-").replace(" ", "-") if wf_name else None
            )
            startup_mode = workflow_config.get("startup_mode")
            trigger_events = workflow_config.get("triggers") or []

            await record_workflow_export(
                app_id=str(app_id),
                user_id=user_id,
                workflow_type="agent-generator",
                repo_url=None,
                job_id=None,
                meta={
                    "bundle_workflow_name": wf_name,
                    "workflow_name": wf_name,
                    "capability_id": capability_id,
                    "agent_names": agent_names,
                    "tool_names": tool_names,
                    "available_agents": agent_names,
                    "available_tools": tool_names,
                },
                extra_fields={
                    "bundle_workflow_name": wf_name,
                    "workflow_name": wf_name,
                    "capability_id": capability_id,
                    "workflow_startup_mode": startup_mode,
                    "trigger_events": trigger_events,
                    "agent_names": agent_names,
                    "tool_names": tool_names,
                    "available_agents": agent_names,
                    "available_tools": tool_names,
                    "agent_websocket_url": websocket_url,
                    "agent_api_url": api_url,
                    "generated_workflow_dir": str(generated_workflow_dir) if generated_workflow_dir else None,
                },
            )

            if context_variables and hasattr(context_variables, "set"):
                try:
                    context_variables.set("agent_websocket_url", websocket_url)
                    context_variables.set("agent_api_url", api_url)
                    context_variables.set("agent_names", agent_names)
                    context_variables.set("tool_names", tool_names)
                    context_variables.set("available_agents", agent_names)
                    context_variables.set("available_tools", tool_names)
                    context_variables.set("generated_workflow_name", wf_name)
                    context_variables.set("generated_workflow_capability_id", capability_id)
                    context_variables.set("generated_workflow_startup_mode", startup_mode)
                    context_variables.set("generated_workflow_trigger_events", trigger_events)
                except Exception:
                    pass
        except Exception as exc:
            wf_logger.debug(f"Failed to record workflow export context for chaining: {exc}")

    async def _record_agent_generator_artifacts(
        *,
        workflow_config: Dict[str, Any],
        created_files: List[str],
        db_intent: Optional[Dict[str, Any]],
        tenancy_requirements: Optional[Dict[str, Any]] = None,
        consistency_requirements: Optional[Dict[str, Any]] = None,
        zip_path: Optional[Path] = None,
    ) -> None:
        try:
            if not app_id:
                return

            if not isinstance(workflow_config, dict):
                return

            extra_files = workflow_config.get("extra_files", [])
            ui_components: List[str] = []
            if isinstance(extra_files, list):
                prefix = f"ChatUI/src/workflows/{wf_name}/components/"
                for item in extra_files:
                    if not isinstance(item, dict):
                        continue
                    rel_path = item.get("path") or item.get("filename")
                    if isinstance(rel_path, str) and rel_path.startswith(prefix):
                        try:
                            ui_components.append(Path(rel_path).stem)
                        except Exception:
                            continue

            artifacts_payload: Dict[str, Any] = {
                "workflow_config": workflow_config,
                "workflow_files": created_files,
                "workflow_orchestrator": {
                    k: v for k, v in workflow_config.items()
                    if k in {
                        "workflow_name",
                        "max_turns",
                        "human_in_the_loop",
                        "startup_mode",
                        "orchestration_pattern",
                        "initial_message_to_user",
                        "initial_message",
                        "initial_agent",
                        "runtime_extensions",
                        "visual_agents",
                    }
                },
                "workflow_agents": workflow_config.get("agents"),
                "workflow_handoffs": workflow_config.get("handoffs"),
                "workflow_tools": workflow_config.get("tools"),
                "workflow_hooks": workflow_config.get("hooks"),
                "workflow_structured_outputs": workflow_config.get("structured_outputs"),
                "workflow_context_variables": workflow_config.get("context_variables"),
                "workflow_ui_config": workflow_config.get("ui_config"),
                "workflow_extra_files": extra_files,
                "workflow_ui_components": ui_components,
            }

            if isinstance(db_intent, dict) and db_intent:
                artifacts_payload["workflow_db_intent"] = db_intent

            # Persist tenancy and consistency requirements for downstream AppGenerator
            if isinstance(tenancy_requirements, dict) and tenancy_requirements:
                artifacts_payload["workflow_tenancy_requirements"] = tenancy_requirements
            if isinstance(consistency_requirements, dict) and consistency_requirements:
                artifacts_payload["workflow_consistency_requirements"] = consistency_requirements

            await record_workflow_artifacts(
                app_id=str(app_id),
                user_id=user_id,
                workflow_type="agent-generator",
                workflow_name=wf_name,
                artifacts=artifacts_payload,
            )

            # Register canonical artifact version so Studio history and refinement paths
            # can reference this build — fires for both CLI and Studio trigger paths.
            try:
                artifact_version = await _register_workflow_bundle_artifact_version(
                    app_id=str(app_id),
                    user_id=user_id,
                    workflow_name="AgentGenerator",
                    chat_id=chat_id,
                    bundle_name=wf_name,
                    zip_path=zip_path,
                    context_variables=context_variables,
                )
                wf_logger.info(f"[ArtifactStore] Registered artifact version {artifact_version.id} for {wf_name}")
            except Exception as av_err:
                wf_logger.warning(f"[ArtifactStore] Failed to register artifact version: {av_err}")

        except Exception as exc:
            wf_logger.debug(f"Failed to record workflow artifacts for chaining: {exc}")

    if not confirmation_only:
        # Immediate mode: Create files NOW, then show download UI
        wf_logger.info("📦 Creating workflow files immediately (confirmation_only=False)...")
        create_res = await create_workflow_files(payload, context_variables)
        if create_res.get("status") != "success":
            return {"status": "error", "message": create_res.get("message", "Failed to create files")}
        created_files = create_res.get("files", [])
        workflow_dir = Path(create_res.get("workflow_dir", "")) if create_res.get("workflow_dir") else None
        workflow_config = create_res.get("workflow_config") or {}
        db_intent_payload = None
        if isinstance(database_intent_data, dict):
            db_intent_payload = database_intent_data.get("DatabaseIntent") or database_intent_data
        
        # Extract tenancy/consistency requirements from StateArchitectAgent output
        tenancy_reqs = None
        consistency_reqs = None
        if isinstance(state_architect_data, dict):
            state_arch = state_architect_data.get("StateArchitecture") or state_architect_data
            if isinstance(state_arch, dict):
                tenancy_reqs = state_arch.get("tenancy_requirements")
                consistency_reqs = state_arch.get("consistency_requirements")

        # Create zip file containing all workflow files
        if workflow_dir and workflow_dir.exists():
            try:
                ui_files = _build_zip_ui_files(workflow_dir=workflow_dir, created_files=created_files)
                zip_path_obj = Path(ui_files[0]["path"]) if ui_files else None
                await _record_agent_generator_context(
                    workflow_config=workflow_config,
                    generated_workflow_dir=workflow_dir,
                )
                _promote_workflow_to_app_workspace(workflow_dir, wf_name)
                await _record_agent_generator_artifacts(
                    workflow_config=workflow_config,
                    created_files=created_files,
                    db_intent=db_intent_payload,
                    tenancy_requirements=tenancy_reqs,
                    consistency_requirements=consistency_reqs,
                    zip_path=zip_path_obj,
                )
                wf_logger.info(f"✅ Prepared zip bundle for download")
            except Exception as zip_err:
                wf_logger.error(f"Failed to create zip file: {zip_err}")
                return {"status": "error", "message": f"Failed to create zip: {zip_err}"}
        else:
            return {"status": "error", "message": "Workflow directory not found"}
    else:
        # Confirmation mode: Show UI first with empty files array, create after user confirms
        wf_logger.info("❓ Asking user confirmation before creating files (confirmation_only=True)...")
        ui_files = []

    ui_payload = {
        "downloadType": "single",  # Always single zip file
        "files": ui_files,
        "agent_message": agent_message_text,
        "description": agent_message_text,
        "title": "Workflow Bundle Ready" if confirmation_only else "Generated Workflow Files",
        "workflow_name": wf_name,
        "agent_message_id": agent_message_id,
        "stage": "confirm" if (confirmation_only and not ui_files) else "files_ready",
    }

    # 5. Emit UI + wait (display mode auto-resolved from tools.yaml)
    try:
        if tlog and _log_tool_event:
            _log_tool_event(tlog, action="emit_ui", status="start", agent_message_id=agent_message_id)
        response = await use_ui_tool(
            tool_id="DownloadCenter",
            payload=ui_payload,
            chat_id=chat_id,
            workflow_name=wf_name,
            # display parameter omitted - auto-resolved from tools.yaml
        )
        wf_logger.info(f"📥 File download UI completed with status: {response.get('status', 'unknown')}")
        if tlog and _log_tool_event:
            _log_tool_event(tlog, action="emit_ui", status="done", result_status=response.get('status', 'unknown'))
    except UIToolError:
        raise
    except Exception as e:
        wf_logger.error(f"❌ UI interaction failed: {e}", exc_info=True)
        raise UIToolError("Failed during file download UI interaction")

    # If user declined -> return early
    if response.get("status") == "cancelled":
        wf_logger.info("❌ User declined download")
        return {
            "status": "cancelled",
            "ui_response": response,
            "agent_message_id": agent_message_id,
            "ui_files": [],
            "message": "User declined download",
        }

    # If confirmation_only, now create the files (user has confirmed)
    if confirmation_only:
        wf_logger.info("✅ User confirmed! Creating files now...")
        create_res = await create_workflow_files(payload, context_variables)
        if create_res.get("status") != "success":
            return {"status": "error", "message": create_res.get("message", "Failed to create files post-confirmation")}
        created_files = create_res.get("files", [])
        workflow_dir = Path(create_res.get("workflow_dir", "")) if create_res.get("workflow_dir") else None
        workflow_config = create_res.get("workflow_config") or {}
        db_intent_payload = None
        if isinstance(database_intent_data, dict):
            db_intent_payload = database_intent_data.get("DatabaseIntent") or database_intent_data
        ui_files = []

        # Extract tenancy/consistency requirements from StateArchitectAgent output
        tenancy_reqs = None
        consistency_reqs = None
        if isinstance(state_architect_data, dict):
            state_arch = state_architect_data.get("StateArchitecture") or state_architect_data
            if isinstance(state_arch, dict):
                tenancy_reqs = state_arch.get("tenancy_requirements")
                consistency_reqs = state_arch.get("consistency_requirements")
        
        # Create zip file containing all workflow files
        if workflow_dir and workflow_dir.exists():
            try:
                ui_files = _build_zip_ui_files(workflow_dir=workflow_dir, created_files=created_files)
                zip_path_obj = Path(ui_files[0]["path"]) if ui_files else None
                await _record_agent_generator_context(
                    workflow_config=workflow_config,
                    generated_workflow_dir=workflow_dir,
                )
                _promote_workflow_to_app_workspace(workflow_dir, wf_name)
                await _record_agent_generator_artifacts(
                    workflow_config=workflow_config,
                    created_files=created_files,
                    db_intent=db_intent_payload,
                    tenancy_requirements=tenancy_reqs,
                    consistency_requirements=consistency_reqs,
                    zip_path=zip_path_obj,
                )
                wf_logger.info(f"✅ Prepared zip bundle for download")
            except Exception as zip_err:
                wf_logger.error(f"Failed to create zip file: {zip_err}")
                return {"status": "error", "message": f"Failed to create zip: {zip_err}"}
        else:
            return {"status": "error", "message": "Workflow directory not found"}

        # Inject files into response data so UI/agent can access them
        if isinstance(response, dict):
            if "data" not in response:
                response["data"] = {}
            if isinstance(response["data"], dict):
                response["data"]["files"] = ui_files
                response["data"]["fileCount"] = len(ui_files)
        if isinstance(response, dict) and "agentContext" not in response:
            response["agentContext"] = {}
        if isinstance(response, dict) and isinstance(response.get("agentContext"), dict):
            response["agentContext"]["files_created"] = True
            response["agentContext"]["file_count"] = len(ui_files)

    # Optional storage only if success & files built
    if storage_backend != "none" and create_res.get("status") == "success" and ui_files:
        try:
            storage_result = await _handle_storage_action(
                storage_backend=storage_backend,
                response=response,
                created_files=create_res.get("files", []),
                workflow_dir=create_res.get("workflow_dir"),
                context_variables=context_variables,
                workflow_name=wf_name,
            )
            if storage_result:
                response = {**response, "storage": storage_result}
                wf_logger.info(f"✅ Storage completed: {storage_result.get('status')}")
                if tlog and _log_tool_event:
                    _log_tool_event(tlog, action="storage", status=str(storage_result.get('status', 'unknown')))
        except Exception as se:
            wf_logger.warning(f"⚠️ Storage action failed: {se}")
            if tlog and _log_tool_event:
                _log_tool_event(tlog, action="storage", status="error", error=str(se))

    final_status = create_res.get("status") if create_res else (response.get("status") or "success")
    
    # Set download_complete context variable if user accepted download
    if context_variables and response.get("download_accepted"):
        try:
            context_variables.set('download_complete', True)
            wf_logger.info("✅ Set download_complete=True (user accepted download)")
        except Exception as ctx_err:
            wf_logger.warning(f"⚠️ Failed to set download_complete context variable: {ctx_err}")

    # Optional GitHub export (triggered by DownloadCenter action)
    deployment_result: Optional[Dict[str, Any]] = None
    try:
        action = None
        if isinstance(response, dict):
            action = response.get("action")
            if not action and isinstance(response.get("data"), dict):
                action = response["data"].get("action")

        if action == "export_to_github":
            zip_bundle_path = None
            for f in ui_files:
                if isinstance(f, dict) and f.get("type") == "zip" and f.get("path"):
                    zip_bundle_path = f.get("path")
                    break

            repo_name = None
            commit_message = "Initial code generation from Mozaiks AI"
            if isinstance(response, dict):
                repo_name = response.get("repo_name") or response.get("repoName")
                commit_message = response.get("commit_message") or response.get("commitMessage") or commit_message
                if isinstance(response.get("data"), dict):
                    repo_name = response["data"].get("repo_name") or response["data"].get("repoName") or repo_name
                    commit_message = (
                        response["data"].get("commit_message")
                        or response["data"].get("commitMessage")
                        or commit_message
                    )

            if not zip_bundle_path:
                deployment_result = {"success": False, "error": "ZIP bundle path not available for export."}
            else:
                wf_logger.info("🚀 Export to GitHub requested", extra={"repo_name": repo_name})
                deployment_result = await export_agent_workflow_to_github(
                    bundle_path=str(zip_bundle_path),
                    app_id=app_id,
                    repo_name=repo_name,
                    commit_message=commit_message,
                    user_id=user_id,
                    context_variables=context_variables,
                )

                if context_variables and isinstance(deployment_result, dict) and deployment_result.get("success"):
                    try:
                        if deployment_result.get("repo_url"):
                            context_variables.set("github_repo_url", deployment_result.get("repo_url"))
                        if deployment_result.get("job_id"):
                            context_variables.set("github_deploy_job_id", deployment_result.get("job_id"))
                    except Exception as ctx_err:
                        wf_logger.warning(f"⚠️ Failed to persist deployment info in context: {ctx_err}")
    except Exception as deploy_err:
        wf_logger.warning(f"⚠️ GitHub export flow failed: {deploy_err}")
    
    return {
        "status": final_status,
        "ui_response": response,
        "agent_message_id": agent_message_id,
        "ui_files": ui_files,
        **({"deployment": deployment_result} if deployment_result is not None else {}),
        **({k: v for k, v in create_res.items() if k not in {"status"}} if create_res else {}),
    }


async def _handle_storage_action(
    storage_backend: str,
    response: Dict[str, Any],
    created_files: List[str],
    workflow_dir: Optional[str],
    context_variables: Optional[Any] = None,
    workflow_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if storage_backend == "local":
        return await _store_files_local(
            response=response,
            created_files=created_files,
            workflow_dir=workflow_dir,
            context_variables=context_variables,
            workflow_name=workflow_name,
        )
    if storage_backend == "none":
        return None
    return {"status": "error", "message": f"Unsupported storage backend: {storage_backend}"}


async def _store_files_local(
    response: Dict[str, Any],
    created_files: List[str],
    workflow_dir: Optional[str],
    context_variables: Optional[Any] = None,
    workflow_name: Optional[str] = None,
) -> Dict[str, Any]:
    selected_path = None
    if isinstance(response, dict):
        selected_path = response.get("selectedPath") or (
            response.get("data", {}).get("selectedPath") if isinstance(response.get("data"), dict) else None
        )
    if not selected_path:
        return {"status": "skipped", "message": "No selectedPath provided by UI"}
    if not created_files:
        return {"status": "skipped", "message": "No files to copy"}

    # Use workflow-specific storage logger
    wf_logger = get_workflow_logger(workflow_name=(workflow_name or "generator"), chat_id=None, app_id=None)
    try:
        dest = Path(selected_path)
        dest.mkdir(parents=True, exist_ok=True)
        copied: List[str] = []
        base_dir = Path(workflow_dir) if workflow_dir else Path.cwd()
        for f in created_files:
            try:
                src = Path(f) if Path(f).is_absolute() else base_dir / f
                if src.exists():
                    target = dest / src.name
                    shutil.copy2(src, target)
                    copied.append(str(target))
                    wf_logger.info(f"📋 Copied {src.name} -> {target}")
            except Exception as e:
                wf_logger.warning(f"⚠️ Failed to copy {f}: {e}")
                continue

        if context_variables:
            try:
                downloads = context_variables.get('file_downloads', []) or []
                rec = {
                    'type': 'local_copy',
                    'files': copied,
                    'file_count': len(copied),
                    'dest_path': str(dest),
                    'copied_at': str(time.time()),
                    'source_files': created_files,
                }
                downloads.append(rec)
                context_variables.set('file_downloads', downloads)
                context_variables.set('last_download', rec)
            except Exception:
                pass

        return {
            "status": "success",
            "message": f"Copied {len(copied)} files to {selected_path}",
            "copied_files": copied,
            "dest_path": str(dest),
            "copy_count": len(copied),
        }
    except Exception as e:
        wf_logger.error(f"❌ Local storage failed: {e}")
        return {"status": "error", "message": f"Failed to copy files: {e}"}
