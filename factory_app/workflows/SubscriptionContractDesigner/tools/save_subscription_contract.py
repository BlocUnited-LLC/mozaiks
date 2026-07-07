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
    return validated.model_dump(mode="python", exclude_none=True)


def _yaml_file_content(config: dict[str, Any]) -> str:
    return str(yaml.safe_dump(
        config,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    ))


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
    config = _normalize_subscription_config(normalized.get("subscription_config_file"))
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

    _cv_set(context_variables, "subscription_contract", normalized)
    _cv_set(context_variables, "subscription_contract_files", normalized.get("code_files") or [])

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
        "app_id": str(app_id),
        "file_count": len(normalized.get("code_files") or []),
        "message": "Subscription contract saved for downstream generator context.",
    }


__all__ = [
    "normalize_subscription_contract",
    "save_subscription_contract",
]
