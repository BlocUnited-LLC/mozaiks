# ==============================================================================
# FILE: workflows/AgentGenerator/tools/collect_api_keys.py  
# DESCRIPTION: Lifecycle tool to extract agent integrations from action_plan context and collect API keys
# TRIGGER: before_agent (ContextVariablesAgent)
# ==============================================================================

"""
Lifecycle Tool: API Key Collection from Action Plan

This lifecycle tool executes before the ContextVariablesAgent runs, extracting
integrations from the action_plan context variable (stored after user approval)
and prompting the user to provide any required API keys.

Flow:
1. Extract Action Plan from context (action_plan context variable)
2. Parse all integrations from workflow phases/agents
3. Deduplicate and normalize service names
4. Prompt the user once for all required API keys via the consolidated UI tool
5. Store collected keys in context for ContextVariablesAgent to use
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, UTC

from logs.logging_config import get_workflow_logger
from mozaiksai.core.transport.simple_transport import SimpleTransport

logger = logging.getLogger(__name__)


__all__ = ["collect_api_keys_from_action_plan"]


def _normalize_service_name(service: str) -> str:
    """Normalize service name to lowercase snake_case."""
    if not service:
        return ""
    # Convert PascalCase to snake_case
    import re
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', service)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

def _extract_integrations_from_action_plan(action_plan: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Extract all integrations that require external API keys from the Action Plan.

    Args:
        action_plan: Action Plan workflow payload from the action_plan context variable.

    Returns:
        Deduplicated list of service metadata dicts containing normalized identifier and display label.
    """
    services: Dict[str, str] = {}

    def _record_service(raw_value: Any) -> None:
        if not raw_value:
            return
        original = str(raw_value).strip()
        if not original:
            return
        normalized = _normalize_service_name(original)
        if not normalized:
            return
        if normalized not in services or not services[normalized]:
            services[normalized] = original or normalized.replace('_', ' ').title()

    try:
        if not isinstance(action_plan, dict):
            return []

        phases = action_plan.get('phases', [])
        if not isinstance(phases, list):
            phases = []

        for phase in phases:
            if not isinstance(phase, dict):
                continue

            agents = phase.get('agents', [])
            if not isinstance(agents, list):
                continue

            for agent in agents:
                if not isinstance(agent, dict):
                    continue
                integrations = agent.get('integrations', [])
                if not isinstance(integrations, list):
                    continue
                for service in integrations:
                    _record_service(service)
    except Exception as e:
        logger.warning(f"Failed to extract integrations from Action Plan: {e}")

    def _sort_key(item: Tuple[str, str]) -> Tuple[str, str]:
        normalized, display = item
        return (display.lower(), normalized)

    sorted_items = sorted(services.items(), key=_sort_key)
    return [
        {"service": normalized, "display_name": display or normalized.replace('_', ' ').title()}
        for normalized, display in sorted_items
    ]


async def collect_api_keys_from_action_plan(context_variables: Any = None) -> Dict[str, Any]:
    """
    Lifecycle tool: Extract integrations from the Action Plan and collect API keys.
    
    This function runs before ContextVariablesAgent executes, extracting all
    third-party services from the approved Action Plan and prompting the user
    to provide API keys for each service.
    
    Args:
        context_variables: AG2 ContextVariables instance with:
            - action_plan: Action Plan from context variable (set after user approval)
            - chat_id, workflow_name, app_id, user_id
    
    Returns:
        Status dict with collected service list
    """
    if not context_variables:
        logger.warning("collect_api_keys_from_action_plan called without context_variables")
        return {"status": "no_context", "services_collected": []}
    
    # Extract context data
    data = getattr(context_variables, 'data', {})
    workflow_name = data.get('workflow_name', 'AgentGenerator')
    chat_id = data.get('chat_id')
    
    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id)
    
    # Get cached Action Plan from context
    action_plan = data.get('action_plan')
    if not action_plan:
        wf_logger.warning("⚠️ No action_plan found in context - skipping API key collection")
        return {"status": "no_action_plan", "services_collected": []}
    
    # Extract integrations
    services = _extract_integrations_from_action_plan(action_plan)

    if not services:
        wf_logger.info("✓ No integrations requiring API keys found in Action Plan - skipping collection")
        return {"status": "no_services_required", "services_collected": []}
    
    wf_logger.info(
        "🔑 Found %s third-party services requiring API keys: %s",
        len(services),
        [svc["display_name"] for svc in services],
    )
    
    # Import consolidated API key bundle helper
    try:
        from workflows.AgentGenerator.tools.request_api_key import request_api_keys_bundle
    except ImportError:
        wf_logger.error("Failed to import request_api_keys_bundle - cannot collect API keys")
        return {"status": "import_error", "services_collected": []}

    # Build payload for consolidated UI interaction
    bundle_services: List[Dict[str, Any]] = []
    for service in services:
        service_identifier = (service.get("service") or "").strip()
        if not service_identifier:
            continue
        display_name = service.get("display_name") or service_identifier.replace('_', ' ').title()
        bundle_services.append(
            {
                "service": service_identifier,
                "display_name": display_name,
                "required": True,
                "mask_input": True,
                "description": f"API key for {display_name} integration",
                "placeholder": f"Enter your {display_name} API key...",
            }
        )

    if not bundle_services:
        wf_logger.info("No valid services resolved for API key bundle request - skipping collection")
        return {"status": "no_services_required", "services_collected": [], "services_failed": []}

    collected_services: List[str] = []
    failed_services: List[str] = []
    collected_details: List[Dict[str, Any]] = []
    failed_details: List[Dict[str, Any]] = []

    try:
        bundle_result = await request_api_keys_bundle(
            services=bundle_services,
            agent_message="Please provide the required API keys so we can configure your workflow.",
            description="We never persist your secrets—only minimal metadata for auditing.",
            context_variables=context_variables,
        )
    except Exception as bundle_error:  # pragma: no cover - defensive guard
        wf_logger.error("❌ API key bundle collection failed: %s", bundle_error, exc_info=True)
        bundle_result = {
            "status": "error",
            "error": str(bundle_error),
            "services": [
                {
                    "service": spec.get("service"),
                    "display_name": spec.get("display_name"),
                    "status": "error",
                    "reason": "bundle_error",
                }
                for spec in bundle_services
            ],
        }

    service_results = bundle_result.get("services") if isinstance(bundle_result, dict) else []
    bundle_status = bundle_result.get("status") if isinstance(bundle_result, dict) else "unknown"
    missing_required = bundle_result.get("missing_required") if isinstance(bundle_result, dict) else []

    wf_logger.info(
        "🔑 API key bundle status: %s (services=%s)",
        bundle_status,
        len(service_results) if isinstance(service_results, list) else 0,
    )
    if missing_required:
        wf_logger.warning("⚠️ Required API keys still missing: %s", missing_required)

    results_by_service: Dict[str, Dict[str, Any]] = {}
    if isinstance(service_results, list):
        for entry in service_results:
            service_key = entry.get("service")
            if isinstance(service_key, str):
                results_by_service[service_key] = entry

    for spec in bundle_services:
        service_identifier = spec.get("service")
        display_name = spec.get("display_name") or service_identifier
        if not service_identifier:
            continue

        result_entry = results_by_service.get(service_identifier, {})
        status_value = result_entry.get("status") or bundle_status
        has_key = bool(result_entry.get("has_key"))

        if has_key and status_value == "success":
            collected_services.append(service_identifier)
            collected_record = {
                "service": service_identifier,
                "display_name": display_name,
                "required": spec.get("required", True),
                "metadata_id": result_entry.get("metadata_id"),
                "metadata_saved": result_entry.get("metadata_saved"),
                "key_length": result_entry.get("key_length"),
                "status": status_value,
            }
            if result_entry.get("metadata_error"):
                collected_record["metadata_error"] = result_entry["metadata_error"]
            collected_details.append(collected_record)
            wf_logger.info("✓ Collected API key metadata for %s", display_name)
            continue

        reason = result_entry.get("reason") or (
            "missing required key" if status_value in {"partial", "no_keys", "missing"} else status_value
        )
        if bundle_status in {"cancelled", "canceled"} and not result_entry:
            reason = "cancelled"

        failed_services.append(service_identifier)
        failed_record = {
            "service": service_identifier,
            "display_name": display_name,
            "reason": reason,
            "status": status_value,
            "required": spec.get("required", True),
        }
        if result_entry.get("metadata_error"):
            failed_record["metadata_error"] = result_entry["metadata_error"]
        failed_details.append(failed_record)
        wf_logger.warning("⚠️ No API key captured for %s (%s)", display_name, reason)

    wf_logger.info(
        "🔑 API key bundle complete: %s collected, %s missing",
        len(collected_services),
        len(failed_services),
    )

    data['api_keys_bundle_result'] = bundle_result
    data['api_keys_bundle_status'] = bundle_status

    # Store collection status in context for downstream agents
    data['api_keys_collected'] = collected_services
    data['api_keys_collected_details'] = collected_details
    data['api_keys_failed'] = failed_services
    data['api_keys_failed_details'] = failed_details
    data['api_keys_collection_complete'] = True
    data['api_keys_collection_timestamp'] = datetime.now(UTC).isoformat()

    # Prepare sanitized .env attachment for downstream download bundle
    try:
        env_lines: List[str] = [
            "# API keys required for the approved action plan",
            f"# Generated at {datetime.now(UTC).isoformat()}",
            "",
        ]

        for detail in collected_details:
            service_name = (detail.get("service") or "").strip()
            if not service_name:
                continue
            env_var = f"{service_name.upper()}_API_KEY"
            env_lines.append(f"{env_var}=")
            metadata_id = detail.get("metadata_id")
            if metadata_id:
                env_lines.append(f"# metadata_id={metadata_id}")
            env_lines.append("")

        if failed_details:
            env_lines.append("# Pending integrations with no key provided during collection")
            for detail in failed_details:
                service_name = (detail.get("service") or "").strip()
                if not service_name:
                    continue
                reason = detail.get("reason") or "not supplied"
                env_lines.append(f"# {service_name.upper()}_API_KEY unresolved: {reason}")

        env_payload = {
            "filename": "api_keys.env",
            "filecontent": "\n".join(env_lines).strip() + "\n",
        }
        data['api_keys_env_attachment'] = env_payload
    except Exception as env_err:  # pragma: no cover - defensive guard
        wf_logger.debug(f"[API_KEYS] Failed to build env attachment: {env_err}")

    # Ensure acceptance flag stays affirmed after collection
    try:
        context_variables.set('action_plan_acceptance', "accepted")  # type: ignore[attr-defined]
    except Exception:
        pass

    # Kick the orchestrator so ContextVariablesAgent can resume
    try:
        transport = await SimpleTransport.get_instance()
        if transport and chat_id:
            await transport.handle_user_input_from_api(
                chat_id=chat_id,
                user_id=data.get('user_id'),
                workflow_name=workflow_name,
                message=None,
                app_id=data.get('app_id'),
            )
            wf_logger.info("[API_KEYS] Resumed workflow after API key collection")
    except Exception as resume_err:
        wf_logger.debug(f"[API_KEYS] Resume signal failed: {resume_err}")

    return {
        "status": "complete",
        "bundle_status": bundle_status,
        "services_collected": collected_services,
        "services_failed": failed_services,
        "total_services": len(bundle_services),
        "missing_required": missing_required,
        "services_details": {
            "collected": collected_details,
            "failed": failed_details,
        },
        "bundle_result": bundle_result,
    }
