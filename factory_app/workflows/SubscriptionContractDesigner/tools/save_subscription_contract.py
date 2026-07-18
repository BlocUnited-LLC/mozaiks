"""Persist a provider-neutral generated-app subscription contract.

This tool validates the LLM-produced contract against the OSS runtime
subscriptions schema and persists a summary artifact. It does not create
subscriptions, payment-provider products, invoices, hosted records, or token
ledger entries.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import yaml

from mozaiksai.core.artifacts import persist_summary_artifact
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool

logger = logging.getLogger(__name__)

_PROPRIETARY_TERMS = (
    "".join(("mozaiks", "pay")),
    "_".join(("hosted", "billing")),
    " ".join(("hosted", "billing")),
    "_".join(("managed", "billing")),
)


def _cv_get(context_variables: Any, key: str) -> Any:
    if context_variables is None:
        return None
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key)
        except Exception:
            return None
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    if isinstance(context_variables, dict):
        return context_variables.get(key)
    return None


def _cv_set(context_variables: Any, key: str, value: Any) -> None:
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


def _extract_output(context_variables: Any) -> dict[str, Any] | None:
    raw = _cv_get(context_variables, "structured_output")
    if not isinstance(raw, dict):
        raw = _cv_get(context_variables, "SubscriptionContractOutput")
    if not isinstance(raw, dict):
        return None
    nested = raw.get("SubscriptionContractOutput")
    if isinstance(nested, dict):
        return nested
    return raw


def _contains_proprietary_term(value: Any) -> str | None:
    text = yaml.safe_dump(value, sort_keys=False, allow_unicode=False).lower()
    for term in _PROPRIETARY_TERMS:
        if term in text:
            return term
    return None


def _normalize_subscription_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("subscription_config_file must be an object when contract_required=true")
    config = dict(raw)
    config.setdefault("schema_version", "mozaiks.subscriptions.v1")
    config.setdefault("assignment_store", None)
    config.setdefault("token_wallets", [])
    config.setdefault("plans", [])
    validated = SubscriptionsConfig.model_validate(config)
    normalized = validated.model_dump(mode="python", exclude_none=True)
    for key in ("token_wallets", "top_up_products", "usage_charge_policies"):
        if normalized.get(key) == []:
            normalized.pop(key, None)
    for plan in normalized.get("plans") or []:
        if not isinstance(plan, dict):
            continue
        if plan.get("usage_limits") == []:
            plan.pop("usage_limits", None)
        if plan.get("token_allowances") == []:
            plan.pop("token_allowances", None)
    return normalized


def _token_wallet_usage_intent_present(
    output: dict[str, Any],
    config: SubscriptionsConfig,
) -> bool:
    if config.top_up_products or config.usage_charge_policies:
        return True
    if any(plan.usage_limits for plan in config.plans):
        return True
    if output.get("metering_declarations"):
        return True
    for update in output.get("module_contract_updates") or []:
        if isinstance(update, dict) and update.get("metering"):
            return True
    for update in output.get("workflow_contract_updates") or []:
        if isinstance(update, dict) and update.get("metering"):
            return True
    return False


def _validate_token_wallet_scope(output: dict[str, Any], config: SubscriptionsConfig) -> None:
    has_plan_allowances = any(plan.token_allowances for plan in config.plans)
    if (has_plan_allowances or config.top_up_products) and not config.token_wallets:
        raise ValueError(
            "token_allowances and top_up_products require declared token_wallets; "
            "subscription-only apps must omit all token wallet fields."
        )
    if not config.token_wallets:
        return
    if not _token_wallet_usage_intent_present(output, config):
        raise ValueError(
            "token_wallets are only valid when the app sells AI usage, credits, "
            "quotas, top-ups, usage charge estimates, or declares metered AI/resource surfaces."
        )


def _yaml_file_content(config: dict[str, Any]) -> str:
    return str(yaml.safe_dump(
        config,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    ))


def _build_review_payload(
    output: dict[str, Any],
    *,
    app_id: str,
    workflow_name: str,
) -> dict[str, Any]:
    config = output.get("subscription_config_file")
    if not isinstance(config, dict):
        config = {}
    files = list(output.get("code_files") or [])
    yaml_preview = ""
    for file in files:
        if isinstance(file, dict) and file.get("filename") == "config/subscriptions.yaml":
            yaml_preview = str(file.get("content") or "")
            break

    return {
        "title": "Subscription Plan Review",
        "app_id": app_id,
        "app_name": output.get("app_name") or app_id,
        "workflow_name": workflow_name,
        "contract_required": bool(output.get("contract_required")),
        "rationale": output.get("rationale") or "",
        "plans": list(config.get("plans") or []),
        "default_plan_id": config.get("default_plan_id"),
        "assignment_store": config.get("assignment_store"),
        "token_wallets": list(config.get("token_wallets") or []),
        "top_up_products": list(config.get("top_up_products") or []),
        "usage_charge_policies": list(config.get("usage_charge_policies") or []),
        "pricing_groups": list((config.get("pricing_catalog") or {}).get("groups") or []),
        "plan_design_rationale": list(output.get("plan_design_rationale") or []),
        "metering_declarations": list(output.get("metering_declarations") or []),
        "module_contract_updates": list(output.get("module_contract_updates") or []),
        "workflow_contract_updates": list(output.get("workflow_contract_updates") or []),
        "page_surface_requirements": list(output.get("page_surface_requirements") or []),
        "generated_files": files,
        "yaml_preview": yaml_preview,
        "forbidden_outputs": list(output.get("forbidden_outputs") or []),
        "validation_notes": list(output.get("validation_notes") or []),
        "review_boundary": {
            "confirmation_label": "Confirm Subscription Plan Contract",
            "change_label": "Request Changes",
            "summary": (
                "This confirms the provider-neutral subscription contract for downstream "
                "app generation. It does not create checkout sessions, assign customers, "
                "grant entitlements, or credit token wallets."
            ),
        },
    }


def _approved_review_response(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    if response.get("approved") is True:
        return True
    action = str(response.get("action") or response.get("status") or "").strip().lower()
    return action in {"confirm", "approved", "approve"}


def _review_change_request(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    for key in ("requested_changes", "rationale", "message"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalized_noop(output: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(output)
    normalized["contract_required"] = False
    normalized["subscription_config_file"] = None
    normalized["plan_design_rationale"] = []
    normalized["metering_declarations"] = []
    normalized["module_contract_updates"] = []
    normalized["workflow_contract_updates"] = []
    normalized["page_surface_requirements"] = []
    normalized["app_generator_instructions"] = list(normalized.get("app_generator_instructions") or [])
    normalized["validation_notes"] = list(normalized.get("validation_notes") or [])
    normalized["forbidden_outputs"] = sorted(
        {
            "config/subscriptions.yaml",
            "contracts/subscriptions.yaml",
            "custom token ledger",
            "custom usage ledger",
            *[str(item) for item in normalized.get("forbidden_outputs") or []],
        }
    )
    normalized["code_files"] = []
    return normalized


def _normalize_required(output: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(output)
    raw_config = normalized.get("subscription_config_file")
    config = _normalize_subscription_config(raw_config)
    _validate_token_wallet_scope(
        normalized,
        SubscriptionsConfig.model_validate(config),
    )
    normalized["subscription_config_file"] = config
    normalized["plan_design_rationale"] = list(normalized.get("plan_design_rationale") or [])
    normalized["code_files"] = [
        {
            "filename": "config/subscriptions.yaml",
            "content": _yaml_file_content(config),
        }
    ]
    forbidden = {
        "contracts/subscriptions.yaml",
        "custom token ledger",
        "custom usage ledger",
        "payment-provider product ids in config/subscriptions.yaml",
        "payment-provider price ids in config/subscriptions.yaml",
    }
    forbidden.update(str(item) for item in normalized.get("forbidden_outputs") or [])
    normalized["forbidden_outputs"] = sorted(forbidden)
    return normalized


def normalize_subscription_contract(output: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate a SubscriptionContractOutput dict."""

    term = _contains_proprietary_term(output)
    if term:
        raise ValueError(f"Subscription contract must be provider-neutral; found proprietary term {term!r}")

    if not bool(output.get("contract_required")):
        return _normalized_noop(output)
    return _normalize_required(output)


async def save_subscription_contract(
    context_variables: Annotated[Any | None, "Runtime context with structured output"] = None,
) -> dict[str, Any]:
    output = _extract_output(context_variables)
    if not isinstance(output, dict):
        return {"success": False, "error": "No SubscriptionContractOutput structured output found"}

    app_id = _cv_get(context_variables, "app_id") or output.get("app_id")
    chat_id = _cv_get(context_variables, "chat_id")
    user_id = _cv_get(context_variables, "user_id")
    build_mode = _cv_get(context_variables, "build_mode")
    workflow_name = _cv_get(context_variables, "workflow_name") or "SubscriptionContractDesigner"

    if not app_id:
        return {"success": False, "error": "app_id required in context or output"}

    try:
        normalized = normalize_subscription_contract(output)
    except Exception as exc:
        return {"success": False, "error": "invalid_subscription_contract", "details": str(exc)}

    review_status = "not_requested_headless"
    review_response: dict[str, Any] | None = None
    if chat_id:
        payload = _build_review_payload(
            normalized,
            app_id=str(app_id),
            workflow_name=str(workflow_name or "SubscriptionContractDesigner"),
        )
        try:
            response = await use_ui_tool(
                "save_subscription_contract",
                payload,
                chat_id=str(chat_id),
                workflow_name=str(workflow_name or "SubscriptionContractDesigner"),
                display="artifact",
            )
        except UIToolError as exc:
            logger.warning("[SubscriptionContractDesigner] Review UI unavailable: %s", exc)
            review_status = "ui_unavailable"
        else:
            review_response = dict(response) if isinstance(response, dict) else {"response": response}
            if not _approved_review_response(response):
                requested_changes = _review_change_request(response)
                _cv_set(context_variables, "subscription_contract", None)
                _cv_set(context_variables, "subscription_contract_files", [])
                _cv_set(context_variables, "subscription_contract_review_status", "changes_requested")
                _cv_set(context_variables, "subscription_contract_review_response", review_response)
                return {
                    "success": False,
                    "review_status": "changes_requested",
                    "requested_changes": requested_changes,
                    "message": (
                        "Subscription contract changes were requested. Revise the "
                        "structured output before downstream generation."
                    ),
                }
            review_status = "confirmed"

    normalized["review_status"] = review_status
    normalized["user_confirmed"] = review_status == "confirmed"
    if review_response:
        normalized["review_response"] = review_response

    _cv_set(context_variables, "subscription_contract", normalized)
    _cv_set(context_variables, "subscription_contract_files", normalized.get("code_files") or [])
    _cv_set(context_variables, "subscription_contract_review_status", review_status)
    if review_response:
        _cv_set(context_variables, "subscription_contract_review_response", review_response)

    try:
        artifact = await persist_summary_artifact(
            app_id=str(app_id),
            artifact_kind="subscription_contract",
            artifact_key="subscription_contract",
            summary_payload=normalized,
            source_workflow=str(workflow_name),
            source_chat_id=str(chat_id) if chat_id else None,
            author_user_id=str(user_id) if user_id else None,
            revision_mode=str(build_mode or "").strip().lower() == "revision",
            input_artifact_kinds=("concept", "build_plan", "design_docs"),
        )
        _cv_set(context_variables, "subscription_contract_artifact_version_id", artifact.id)
    except Exception as exc:
        logger.warning("[SubscriptionContractDesigner] Artifact persistence failed: %s", exc)

    return {
        "success": True,
        "contract_required": bool(normalized.get("contract_required")),
        "review_status": review_status,
        "app_id": str(app_id),
        "file_count": len(normalized.get("code_files") or []),
        "message": "Subscription contract saved for downstream generator context.",
    }


__all__ = [
    "_build_review_payload",
    "normalize_subscription_contract",
    "save_subscription_contract",
]
