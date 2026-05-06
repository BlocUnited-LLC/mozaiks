# ==============================================================================
# FILE: workflows/AgentGenerator/tools/request_api_key.py
# DESCRIPTION: UI tool function to request an external service API key from the user.
#              Never logs, returns, or echoes any portion (even masked) of the API key.
# RUNTIME PARAMS (injected via **runtime): chat_id, app_id, workflow_name, context_variables.
# ==============================================================================
import uuid
from typing import Any, Dict, Optional, Annotated, List

from logs.logging_config import get_workflow_logger
from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool
from mozaiksai.core.workflow.generator_support.connector_service import record_connector_metadata


__all__ = ["request_api_key", "request_api_keys_bundle"]


async def _persist_connector_metadata(
    *,
    app_id: Optional[str],
    user_id: Optional[str],
    service_norm: str,
    display_name: str,
    key_length: Optional[int],
    workflow_name: Optional[str],
    chat_id: Optional[str],
    agent_message_id: Optional[str],
    ui_event_id: Optional[str],
    wf_logger,
) -> Dict[str, Any]:
    """Persist sanitized connector metadata through the platform connector service."""

    result: Dict[str, Any] = {
        "saved": False,
        "connector": None,
        "error": None,
    }

    if not app_id:
        result["error"] = "app_id is required to persist connector metadata"
        return result

    try:
        record_result = await record_connector_metadata(
            app_id=str(app_id),
            user_id=str(user_id) if user_id else None,
            service=service_norm,
            display_name=display_name,
            key_length=int(key_length or 0),
            workflow_name=workflow_name,
            chat_id=chat_id,
            agent_message_id=agent_message_id,
            ui_event_id=ui_event_id,
            logger=wf_logger,
        )
        result["saved"] = True
        result["connector"] = record_result.get("connector")
        return result
    except Exception as persist_err:
        wf_logger.warning(f"⚠️ Connector metadata save failed (non-critical): {persist_err}")
        result["error"] = str(persist_err)
        return result


def _extract_agent_name(container: Any) -> Optional[str]:
    """Best-effort agent attribution lookup from context variables."""
    if not container or not hasattr(container, "get"):
        return None

    candidate_keys = (
        "agent_name",
        "agentName",
        "turn_agent_name",
        "turn_agent",
        "auto_tool_agent_name",
        "auto_tool_agent",
        "last_agent_name",
        "speaker",
        "sender",
    )
    for key in candidate_keys:
        try:
            value = container.get(key)
        except Exception:  # pragma: no cover - defensive guard
            continue
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


async def request_api_key(
    service: Annotated[str, "Lowercase service identifier (e.g. 'openai', 'anthropic', 'huggingface')"],
    agent_message: Annotated[Optional[str], "Mandatory short sentence displayed in the chat along with the artifact for context."] = None,
    description: Optional[str] = None,
    required: Annotated[bool, "Whether key is required to proceed."] = True,
    mask_input: Annotated[bool, "Whether to mask characters in UI input field."] = True,
    display_name: Annotated[
        Optional[str],
        "Human-friendly service label shown to the user (e.g. 'OpenAI', 'Anthropic Claude'). Defaults to a title-cased version of the identifier.",
    ] = None,
    store_connector: Annotated[
        bool,
        "When true, persist the received secret into the connector store for later runtime use.",
    ] = False,
    return_for_e2b: Annotated[
        bool,
        "When true, include the submitted secret in the tool result under `_secret_for_e2b` for immediate sandbox use.",
    ] = False,
    # AG2-native context injection
    context_variables: Annotated[Optional[Any], "Context variables provided by AG2"] = None,
) -> Dict[str, Any]:
    """Emit a UI interaction prompting the user to input an API key.

    Behavior:
      1. Builds a UI payload for the React component `AgentAPIKeyInput`.
      2. Emits the interactive UI via `use_ui_tool(...)`.
      3. Waits for the correlated frontend response.
      4. Optionally stores the connector secret and always saves sanitized metadata.
      5. Returns a sanitized result (never includes the secret itself unless `return_for_e2b=true`).

    SECURITY:
      - Does NOT log the provided key.
      - Does NOT return raw or masked fragments of the key.
      - Only metadata (length, status) is returned.
      - If saved to database, only metadata is stored (never the actual key).

    CONNECTOR METADATA:
      - Automatically saves sanitized connector metadata for the current app.
      - Never stores the actual API key in MongoDB.
      - Connector metadata is platform-owned and surfaces in Studio/Admin adapters.
      - Actual secret persistence requires a vault-backed connector implementation.
    """
    # Extract parameters from AG2 ContextVariables
    chat_id: Optional[str] = None
    workflow_name: Optional[str] = None
    agent_name: Optional[str] = None
    
    if context_variables and hasattr(context_variables, 'get'):
        chat_id = context_variables.get('chat_id')
        workflow_name = context_variables.get('workflow_name')
        agent_name = _extract_agent_name(context_variables)

    if not workflow_name:
        return {"status": "error", "message": "workflow_name is required for request_api_key"}

    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id)
    if not isinstance(service, str) or not service.strip():
        return {"status": "error", "message": "service is required"}
    service_norm = service.strip().lower().replace(" ", "_")
    display_name = display_name.strip() if isinstance(display_name, str) else None
    if not display_name:
        # Preserve original casing when available; fall back to prettified identifier.
        display_name = service.strip() if isinstance(service, str) else service_norm
        if display_name == service_norm:
            display_name = service_norm.replace("_", " ").title()
    # Optional: tool-scoped logger
    try:
        from logs.tools_logs import get_tool_logger as _get_tool_logger, log_tool_event as _log_tool_event  # type: ignore
        tlog = _get_tool_logger(tool_name="RequestAPIKey", chat_id=chat_id, workflow_name=workflow_name)
        _log_tool_event(tlog, action="start", status="ok", service=service_norm, service_display=display_name)
    except Exception:
        tlog = None  # type: ignore

    agent_message_id = f"msg_{uuid.uuid4().hex[:10]}"

    payload: Dict[str, Any] = {
        "service": service_norm,
        "service_display_name": display_name,
        "label": f"{display_name} API Key",
        "agent_message": agent_message or f"Please provide your {display_name} API key to continue.",
        "description": description or f"Enter your {display_name} API key to continue",
        "placeholder": f"Enter your {display_name} API key...",
        "required": required,
        "maskInput": mask_input,
        "agent_message_id": agent_message_id,
    }
    if agent_name:
        payload["agent_name"] = agent_name
        payload["agentName"] = agent_name
        payload["agent"] = agent_name

    # Optimized path: use unified helper to emit + wait
    try:
        # Emit UI tool and wait for response (display mode auto-resolved from tools.yaml)
        if 'tlog' in locals() and tlog:
            try:
                from logs.tools_logs import log_tool_event as _log_tool_event  # type: ignore
                _log_tool_event(tlog, action="emit_ui", status="start")
            except Exception:
                pass
        response = await use_ui_tool(
            "AgentAPIKeyInput",
            payload,
            chat_id=chat_id,
            workflow_name=str(workflow_name),
            # display parameter omitted - auto-resolved from tools.yaml
        )
        if 'tlog' in locals() and tlog:
            try:
                from logs.tools_logs import log_tool_event as _log_tool_event  # type: ignore
                _log_tool_event(
                    tlog,
                    action="emit_ui",
                    status="done",
                    result_status=(response or {}).get("status", "unknown"),
                    service_display=display_name,
                )
            except Exception:
                pass
    except UIToolError as e:
        return {"status": "error", "message": f"UI interaction failed: {e}"}
    except Exception as e:  # pragma: no cover
        wf_logger.error(f"❌ API key UI interaction failed: {e}")
        return {"status": "error", "message": "UI interaction failure"}

    # Normalize response structure
    status = (response or {}).get("status") or (response or {}).get("data", {}).get("status") or "unknown"

    # Detect cancellation / error early
    if status in {"cancelled", "canceled"}:
        return {
            "status": "cancelled",
            "service": service_norm,
            "service_display_name": display_name,
            "agent_message_id": agent_message_id,
            "ui_event_id": (response or {}).get("event_id"),
        }
    if status == "error":
        return {
            "status": "error",
            "service": service_norm,
            "message": (response or {}).get("error") or "User submission error",
            "service_display_name": display_name,
            "agent_message_id": agent_message_id,
            "ui_event_id": (response or {}).get("event_id"),
        }

    # Extract (without retaining) the key to compute metadata if present
    api_key = None
    try:
        data_block = response.get("data") if isinstance(response, dict) else None
        if isinstance(data_block, dict):
            api_key = data_block.get("apiKey") or data_block.get("api_key")
    except Exception:
        api_key = None

    key_length = len(api_key) if isinstance(api_key, str) else None

    app_id = context_variables.get("app_id") if context_variables and hasattr(context_variables, "get") else None
    user_id = context_variables.get("user_id") if context_variables and hasattr(context_variables, "get") else None

    # Attempt durable connector storage when the runtime provides a secret-backed connector implementation
    connector_result = None
    if api_key and store_connector and context_variables:
        if app_id:
            try:
                from mozaiksai.core.workflow.generator_support.connector_service import store_connector as do_store
                connector_result = await do_store(
                    app_id=str(app_id),
                    user_id=str(user_id) if user_id else "unknown",
                    service=service_norm,
                    secret_value=api_key,
                    display_name=display_name,
                    ttl_days=30,
                    logger=wf_logger
                )
                if connector_result.get("success"):
                    wf_logger.info(f"Connector stored: {service_norm}")
                else:
                    wf_logger.warning(f"Connector storage failed: {connector_result.get('error')}")
            except Exception as conn_err:
                wf_logger.warning(f"Connector storage error: {conn_err}")

    # Prepare return data
    result = {
        "status": "success",
        "service": service_norm,
        "service_display_name": display_name,
        "agent_message_id": agent_message_id,
        "ui_event_id": (response or {}).get("event_id"),
        "has_key": bool(api_key),
        "key_length": key_length,
        "connector_stored": connector_result.get("success") if connector_result else False,
        "connector_metadata_saved": connector_result.get("metadata_saved") if connector_result else False,
        "connector_expires_at": connector_result.get("expires_at") if connector_result else None,
        "_secret_for_e2b": api_key if return_for_e2b else None,  # Only populated when explicitly requested
    }

    # Save connector metadata (NEVER the actual key)
    if api_key:
        persist_result = await _persist_connector_metadata(
            app_id=str(app_id) if app_id else None,
            user_id=str(user_id) if user_id else None,
            service_norm=service_norm,
            display_name=display_name,
            key_length=key_length,
            workflow_name=workflow_name,
            chat_id=chat_id,
            agent_message_id=agent_message_id,
            ui_event_id=(response or {}).get("event_id"),
            wf_logger=wf_logger,
        )
        result["metadata_saved"] = persist_result.get("saved", False)
        if persist_result.get("connector"):
            result["connector"] = persist_result["connector"]
        if persist_result.get("error"):
            result["metadata_error"] = persist_result["error"]
    else:
        result["metadata_saved"] = False

    return result


async def request_api_keys_bundle(
    services: List[Dict[str, Any]],
    *,
    agent_message: Optional[str] = None,
    description: Optional[str] = None,
    context_variables: Optional[Any] = None,
) -> Dict[str, Any]:
    """Collect multiple API keys via a single UI tool interaction."""

    if not services:
        return {"status": "no_services", "services": []}

    chat_id: Optional[str] = None
    workflow_name: Optional[str] = None
    agent_name: Optional[str] = None

    if context_variables and hasattr(context_variables, "get"):
        chat_id = context_variables.get("chat_id")
        workflow_name = context_variables.get("workflow_name")
        agent_name = _extract_agent_name(context_variables)

    if not workflow_name:
        return {"status": "error", "message": "workflow_name is required"}

    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id)

    normalized_services: List[Dict[str, Any]] = []
    agent_message_id = f"bundle_{uuid.uuid4().hex[:10]}"

    for idx, raw_service in enumerate(services):
        identifier = str(raw_service.get("service", "")).strip()
        if not identifier:
            continue
        service_norm = identifier.lower().replace(" ", "_")
        display_name = raw_service.get("display_name") or raw_service.get("displayName")
        if not display_name:
            display_name = identifier.replace("_", " ").title()

        required = bool(raw_service.get("required", True))
        mask_input = bool(raw_service.get("mask_input", raw_service.get("maskInput", True)))
        placeholder = raw_service.get("placeholder") or f"Enter your {display_name} API key..."
        per_service_agent_msg_id = raw_service.get("agent_message_id") or f"{agent_message_id}:{service_norm}:{idx}"

        normalized_services.append(
            {
                "service": service_norm,
                "display_name": display_name,
                "required": required,
                "mask_input": mask_input,
                "placeholder": placeholder,
                "description": raw_service.get("description") or f"API key for {display_name}",
                "label": raw_service.get("label") or f"{display_name} API Key",
                "agent_message_id": per_service_agent_msg_id,
            }
        )

    if not normalized_services:
        wf_logger.info("No valid services resolved for API key bundle request")
        return {"status": "no_services", "services": []}

    payload_services = [
        {
            "service": svc["service"],
            "service_display_name": svc["display_name"],
            "required": svc["required"],
            "maskInput": svc["mask_input"],
            "placeholder": svc["placeholder"],
            "description": svc["description"],
            "label": svc["label"],
            "agent_message_id": svc["agent_message_id"],
        }
        for svc in normalized_services
    ]

    payload: Dict[str, Any] = {
        "agent_message_id": agent_message_id,
        "agent_message": agent_message
        or "Provide the required API keys so the workflow can continue.",
        "description": description
        or "Your keys are not persisted by the runtime; only metadata is logged.",
        "services": payload_services,
    }
    if agent_name:
        payload["agent_name"] = agent_name
        payload["agentName"] = agent_name

    try:
        from logs.tools_logs import get_tool_logger as _get_tool_logger, log_tool_event as _log_tool_event  # type: ignore

        tlog = _get_tool_logger(
            tool_name="RequestAPIKeysBundle",
            chat_id=chat_id,
            workflow_name=workflow_name,
        )
        _log_tool_event(
            tlog,
            action="start",
            status="ok",
            services=[svc["service"] for svc in normalized_services],
        )
    except Exception:
        tlog = None  # type: ignore

    try:
        response = await use_ui_tool(
            "AgentAPIKeysBundleInput",
            payload,
            chat_id=chat_id,
            workflow_name=str(workflow_name),
        )
        if tlog:
            try:
                from logs.tools_logs import log_tool_event as _log_tool_event  # type: ignore

                _log_tool_event(
                    tlog,
                    action="emit_ui",
                    status="done",
                    event_id=response.get("ui_event_id"),
                )
            except Exception:
                pass
    except UIToolError as exc:
        return {"status": "error", "message": f"UI interaction failed: {exc}"}
    except Exception as exc:  # pragma: no cover - defensive guard
        wf_logger.error(f"❌ API key bundle UI interaction failed: {exc}")
        return {"status": "error", "message": "UI interaction failure"}

    status = (response or {}).get("status") or (response or {}).get("data", {}).get("status")
    data_block = response.get("data") if isinstance(response, dict) else {}
    submitted_services = data_block.get("services") if isinstance(data_block, dict) else None

    if status in {"cancelled", "canceled"}:
        return {
            "status": "cancelled",
            "services": [
                {
                    "service": svc["service"],
                    "display_name": svc["display_name"],
                    "required": svc["required"],
                }
                for svc in normalized_services
            ],
        }

    if status == "error":
        return {
            "status": "error",
            "message": (response or {}).get("error") or "User submission error",
            "services": [],
        }

    submitted_lookup: Dict[str, Dict[str, Any]] = {}
    if isinstance(submitted_services, list):
        for item in submitted_services:
            service_id = str(item.get("service", "")).strip().lower().replace(" ", "_")
            if service_id:
                submitted_lookup[service_id] = item

    sanitized_services: List[Dict[str, Any]] = []
    missing_required: List[str] = []
    collected: List[str] = []

    for svc in normalized_services:
        entry = submitted_lookup.get(svc["service"], {})
        raw_key = entry.get("apiKey") or entry.get("api_key")
        trimmed_key = raw_key.strip() if isinstance(raw_key, str) else ""
        key_length = len(trimmed_key) if trimmed_key else 0
        has_key = bool(trimmed_key)

        app_id = context_variables.get("app_id") if context_variables and hasattr(context_variables, "get") else None
        user_id = context_variables.get("user_id") if context_variables and hasattr(context_variables, "get") else None
        metadata_out: Dict[str, Any] = {"saved": False, "connector": None, "error": None}
        connector_stored = False
        connector_metadata_saved = False
        if has_key:
            metadata_out = await _persist_connector_metadata(
                app_id=str(app_id) if app_id else None,
                user_id=str(user_id) if user_id else None,
                service_norm=svc["service"],
                display_name=svc["display_name"],
                key_length=key_length,
                workflow_name=workflow_name,
                chat_id=chat_id,
                agent_message_id=svc["agent_message_id"],
                ui_event_id=response.get("ui_event_id"),
                wf_logger=wf_logger,
            )
            connector_metadata_saved = metadata_out.get("saved", False)
            
            # Attempt durable connector storage in addition to sanitized metadata persistence
            if app_id and trimmed_key:
                try:
                    from mozaiksai.core.workflow.generator_support.connector_service import store_connector
                    conn_result = await store_connector(
                        app_id=str(app_id),
                        user_id=str(user_id) if user_id else "unknown",
                        service=svc["service"],
                        secret_value=trimmed_key,
                        display_name=svc["display_name"],
                        ttl_days=30,
                        logger=wf_logger
                    )
                    connector_stored = conn_result.get("success", False)
                    connector_metadata_saved = connector_metadata_saved or conn_result.get("metadata_saved", False)
                    if connector_stored:
                        wf_logger.info(f"✓ Connector stored for {svc['service']}")
                except Exception as conn_err:
                    wf_logger.warning(f"Connector storage failed for {svc['service']}: {conn_err}")

        status_value = "success" if has_key else ("missing" if svc["required"] else "skipped")
        if svc["required"] and not has_key:
            missing_required.append(svc["service"])
        if has_key:
            collected.append(svc["service"])

        sanitized_entry: Dict[str, Any] = {
            "service": svc["service"],
            "display_name": svc["display_name"],
            "required": svc["required"],
            "status": status_value,
            "has_key": has_key,
            "key_length": key_length,
            "metadata_saved": metadata_out.get("saved", False),
            "connector_stored": connector_stored,
            "connector_metadata_saved": connector_metadata_saved,
        }
        if metadata_out.get("connector"):
            sanitized_entry["connector"] = metadata_out["connector"]
        if metadata_out.get("error"):
            sanitized_entry["metadata_error"] = metadata_out["error"]
        if not has_key:
            sanitized_entry["reason"] = entry.get("reason") or (
                "missing required key" if svc["required"] else "not provided"
            )

        sanitized_services.append(sanitized_entry)

        # Ensure sensitive value is not kept alive in memory longer than necessary
        if isinstance(entry, dict) and "apiKey" in entry:
            entry["apiKey"] = None

    overall_status = "success" if not missing_required else ("partial" if collected else "no_keys")

    result: Dict[str, Any] = {
        "status": overall_status,
        "services": sanitized_services,
        "collected": collected,
        "missing_required": missing_required,
        "submitted_at": data_block.get("submissionTime") if isinstance(data_block, dict) else None,
        "ui_event_id": response.get("ui_event_id"),
    }

    if tlog:
        try:
            from logs.tools_logs import log_tool_event as _log_tool_event  # type: ignore

            _log_tool_event(
                tlog,
                action="complete",
                status=overall_status,
                collected=len(collected),
                missing=len(missing_required),
            )
        except Exception:
            pass

    return result

