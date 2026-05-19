# ==============================================================================
# FILE: workflows/AgentGenerator/tools/get_or_request_api_key.py
# DESCRIPTION: AG2 tool that checks for existing connector before prompting user.
#              If connector exists and has reusable secret storage, returns the secret.
#              If expired, metadata-only, or missing, prompts user via UI tool.
# ==============================================================================
from typing import Annotated, Any, Dict, Optional

from autogen.tools.dependency_injection import Field

from logs.logging_config import get_workflow_logger

__all__ = ["get_or_request_api_key"]


def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key, default)
        except TypeError:
            return context_variables.get(key) or default
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


async def get_or_request_api_key(
    service: Annotated[
        str,
        Field(description="The normalized connector service id, such as model_provider, payment_provider, or email_provider."),
    ],
    display_name: Annotated[
        Optional[str],
        Field(description="Human-readable service label shown in the UI."),
    ] = None,
    context_variables: Annotated[
        Optional[Any],
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> Dict[str, Any]:
    """
    Smart API key retrieval:
    1. Check if active connector exists for this app + service
    2. If YES: fetch reusable secret from the connector service, return for E2B use
    3. If NO/EXPIRED: prompt user via UI, persist sanitized connector metadata, and attempt durable storage when available
    
    This is the PRIMARY tool agents should use for API keys.
    
    Returns:
        {
            "status": "success" | "error" | "user_cancelled",
            "service": str,
            "source": "existing_connector" | "new_from_user",
            "secret_value": str or None (THE KEY - for E2B only!),
            "connector_status": "active" | "expiring" | "expired" | "new",
            "days_until_expiry": int or None,
            "message": str (for agent to relay to user)
        }
    """
    workflow_name = _context_get(context_variables, "workflow_name", "AgentGenerator")
    wf_logger = get_workflow_logger(workflow_name)
    
    # Get app_id from context
    app_id = None
    if context_variables:
        app_id = _context_get(context_variables, "app_id")
    
    if not app_id:
        return {
            "status": "error",
            "service": service,
            "error": "No app_id in context - cannot manage connectors",
            "message": "I couldn't identify which app this is for. Please ensure you're in an active app session."
        }
    
    service_norm = service.lower().strip()
    display = display_name or service_norm.title()
    
    # Step 1: Check existing connector
    wf_logger.info(f"Checking connector status for {service_norm} (app: {app_id})")
    
    try:
        from mozaiksai.core.workflow.generator_support.connector_service import (
            get_connector_status,
            get_secret_for_e2b,
            store_connector,
        )
        
        status = await get_connector_status(app_id, service_norm, wf_logger)
        
        # Case A: Active connector exists
        if status["exists"] and status["status"] in ("active", "expiring"):
            wf_logger.info(f"Found active connector for {service_norm}")
            
            # Fetch the reusable secret from the configured connector backend.
            secret_result = await get_secret_for_e2b(app_id, service_norm, wf_logger)
            
            if secret_result["success"]:
                days_left = status.get("days_until_expiry")
                message = f"Using your existing {display} connection."
                if status["status"] == "expiring" and days_left is not None:
                    message = f"Using your existing {display} connection (expires in {days_left} days - consider refreshing soon)."
                
                return {
                    "status": "success",
                    "service": service_norm,
                    "source": "existing_connector",
                    "secret_value": secret_result["secret_value"],
                    "connector_status": status["status"],
                    "days_until_expiry": days_left,
                    "key_prefix": status.get("connector", {}).get("keyPrefix"),
                    "message": message
                }
            else:
                # Secret fetch failed - need to re-request
                wf_logger.warning(f"Connector exists but KV fetch failed: {secret_result.get('error')}")
        
        # Case B: Expired connector
        if status["exists"] and status["status"] == "expired":
            wf_logger.info(f"Connector expired for {service_norm}, requesting new key")
            prompt_reason = f"Your {display} API key has expired. Please enter a new one to continue."
        
        # Case B2: Metadata record exists but no secret is available in the runtime
        elif status["exists"] and status["status"] == "metadata_only":
            wf_logger.info(f"Connector metadata exists for {service_norm}, but no reusable secret is available")
            prompt_reason = f"We have a saved {display} connector record, but this runtime still needs a fresh key for the current session."
        
        # Case C: No connector
        elif not status["exists"]:
            wf_logger.info(f"No connector found for {service_norm}, requesting from user")
            prompt_reason = f"Please connect your {display} account to continue."
        
        else:
            prompt_reason = f"Please provide your {display} API key."
        
    except Exception as e:
        wf_logger.warning(f"Connector check failed: {e}, falling back to request")
        prompt_reason = f"Please provide your {display} API key."
    
    # Step 2: Request from user via UI tool
    wf_logger.info(f"Prompting user for {service_norm} key")
    
    try:
        from .request_api_key import request_api_key
        
        ui_result = await request_api_key(
            service=service_norm,
            display_name=display,
            store_connector=True,  # Attempt durable connector storage in addition to metadata persistence
            return_for_e2b=True,   # Return key for E2B use
            context_variables=context_variables,
        )
        
        if ui_result.get("status") != "success" or not ui_result.get("has_key"):
            return {
                "status": "user_cancelled" if ui_result.get("status") == "cancelled" else "error",
                "service": service_norm,
                "source": "user_declined",
                "secret_value": None,
                "message": f"No {display} key was provided. Some features may not work without it."
            }
        
        # Extract key from UI result (only present with return_for_e2b=True)
        secret_value = ui_result.get("_secret_for_e2b")
        connector_stored = ui_result.get("connector_stored", False)
        
        return {
            "status": "success",
            "service": service_norm,
            "source": "new_from_user",
            "secret_value": secret_value,  # Available for E2B
            "connector_status": "new",
            "connector_stored": connector_stored,
            "days_until_expiry": 30,
            "message": f"{display} connected successfully." if connector_stored else f"{display} key received (connector storage pending)."
        }
        
    except Exception as e:
        wf_logger.error(f"UI request failed: {e}")
        return {
            "status": "error",
            "service": service_norm,
            "error": str(e),
            "message": f"Failed to connect {display}. Please try again."
        }
