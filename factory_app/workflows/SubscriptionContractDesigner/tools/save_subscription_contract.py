"""Persist a provider-neutral generated-app subscription contract.

This tool validates the LLM-produced contract against the OSS runtime
subscriptions schema and persists a summary artifact. It does not create
subscriptions, payment-provider products, invoices, hosted records, or token
ledger entries.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Annotated, Any

import yaml

from mozaiksai.core.artifacts import persist_summary_artifact
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.workflow.context.frozen import detach
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
    raw = detach(_cv_get(context_variables, "structured_output"))
    if not isinstance(raw, dict):
        return None
    return raw


def _is_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _concept_requires_contract(context_variables: Any) -> str | None:
    """Why the approved concept already answered the contract question, or None.

    Reads the frozen context the way it arrives: every live container returns
    a read-only mapping, so this tests Mapping, never dict, and detaches first.
    A `dict` test here would silently disarm the guard on every real build,
    which is exactly how DesignDocs' surface guard was dead until #708.
    """
    if not _is_enabled(_cv_get(context_variables, "monetization_enabled")):
        return None
    if str(_cv_get(context_variables, "brownfield_build_path") or "").strip():
        # The existing app may already own billing; the prompt asks the designer
        # to name that surface in rationale instead of being forced here.
        return None
    blueprint = detach(_cv_get(context_variables, "concept_blueprint"))
    if not isinstance(blueprint, Mapping):
        return None
    intent = blueprint.get("monetization_intent")
    if not isinstance(intent, Mapping):
        return None
    if intent.get("monetized") is not True or intent.get("subscription_contract_likely") is not True:
        return None
    summary = str(intent.get("money_flow_summary") or "").strip()
    return (
        "The approved concept records monetization_intent.monetized=true and "
        "monetization_intent.subscription_contract_likely=true"
        + (f" ({summary})" if summary else "")
        + ", and monetization is enabled for this build. That is the concept's own "
        "determination that the app sells recurring access, gated features, quotas, "
        "or credits, so contract_required must be true. Design the plan ladder from "
        "money_flow_summary, likely_revenue_models, and the gated surfaces; the "
        "absence of an explicit plan list upstream is not a reason to refuse."
    )


def _request_changes(context_variables: Any, requested_changes: str | None, *, source: str) -> dict[str, Any]:
    """Return the turn to the designer with the reason where it can read it.

    Mirrors the review UI's request_changes path so the transition graph, the
    attempt budget, and the agent's next turn all see one shape. The reason is
    also returned as `error`: tool-outcome validation logs a rejection only when
    the payload carries one, and a refusal the log never records is how the
    DesignDocs guard stayed dead until #708.
    """
    requested_changes = (requested_changes or "").strip() or "Revise the subscription contract."
    review_response = {
        "action": "request_changes",
        "approved": False,
        "status": "changes_requested",
        "requested_changes": requested_changes,
        "source": source,
    }
    _cv_set(context_variables, "subscription_contract", None)
    _cv_set(context_variables, "subscription_contract_files", [])
    _cv_set(context_variables, "subscription_contract_review_status", "changes_requested")
    _cv_set(context_variables, "subscription_contract_review_response", review_response)
    return {
        "success": False,
        "review_status": "changes_requested",
        "requested_changes": requested_changes,
        "error": requested_changes,
        "message": (
            "Subscription contract changes were requested. Revise the "
            "structured output before downstream generation."
        ),
    }


def _contains_proprietary_term(value: Any) -> str | None:
    text = yaml.safe_dump(value, sort_keys=False, allow_unicode=False).lower()
    for term in _PROPRIETARY_TERMS:
        if term in text:
            return term
    return None


# assignment_store fields whose explicit null is meaning-bearing rather than
# merely absent. `exclude_none=True` keeps the normalized contract compact,
# but for these it would turn a deliberate opt-out into a silent opt-in on the
# next reload, because an absent key falls back to the model default.
_NULL_MEANING_ASSIGNMENT_FIELDS = ("revision_field",)


def _restore_explicit_nulls(validated: Any, normalized: dict[str, Any]) -> None:
    """Re-add assignment-store nulls the caller set on purpose."""
    store = getattr(validated, "assignment_store", None)
    if store is None or not isinstance(normalized.get("assignment_store"), dict):
        return
    explicitly_set: set[str] = getattr(store, "model_fields_set", set())
    for field in _NULL_MEANING_ASSIGNMENT_FIELDS:
        if field in explicitly_set and getattr(store, field, None) is None:
            normalized["assignment_store"][field] = None


def _degraded_pricing_catalog(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """Reduce an optional pricing catalog to the part that is actually valid.

    `pricing_catalog` is display metadata for pricing tabs. The runtime never
    reads it to decide entitlement -- `ConfiguredEntitlementAdapter` answers
    from `plans[].capabilities`. But `PricingCatalogDef` and the config-level
    validators reject ten distinct malformations in it, and each one fails the
    whole contract, so a monetized build dies over which tab opens first.

    Two live runs, two different malformations, same optional field:

        82f87eb3  {"default_group_id": null, "groups": []}
        (#711)    -> "pricing_catalog.groups must be non-empty"

        82f87eb3  {"default_group_id": "default", "groups": [{"group_id": "basic"}]}
        +main349  -> "default_group_id 'default' must reference a declared
                      pricing_catalog group_id; known group_ids: ['basic']"

    #711 fixed the first and deliberately kept the second fatal. That line was
    wrong: it sorted by "empty vs malformed" when the question is whether the
    field carries app meaning. Dropping an unresolvable tab preference loses
    nothing a user can observe; failing the build loses the whole contract.

    So the catalog degrades to a valid subset rather than failing:
      - a group's plan_ids/add_on_ids that name nothing declared are dropped
      - a group with no renderable label, or left with neither, is dropped
      - duplicate group_ids keep the first
      - a default_group_id naming no surviving group is dropped
      - no surviving groups means no catalog

    Anything that carries contract meaning -- plans, capabilities, wallets,
    assignment_store -- stays strict and is untouched here.
    """
    catalog = config.get("pricing_catalog")
    if not isinstance(catalog, Mapping):
        return None

    known_plans = {
        plan.get("plan_id")
        for plan in config.get("plans") or []
        if isinstance(plan, Mapping) and plan.get("plan_id")
    }
    known_add_ons = {
        product.get("add_on_id")
        for product in config.get("add_on_products") or []
        if isinstance(product, Mapping) and product.get("add_on_id")
    }

    kept: list[dict[str, Any]] = []
    seen_group_ids: set[str] = set()
    for group in catalog.get("groups") or []:
        if not isinstance(group, Mapping):
            continue
        group_id = group.get("group_id")
        if not isinstance(group_id, str) or not group_id.strip() or group_id in seen_group_ids:
            continue
        label = group.get("label")
        if not isinstance(label, str) or not label.strip():
            # A tab with no label cannot be rendered. Dropping it keeps this a
            # subset of what the agent sent; deriving a label from group_id
            # would be inventing display copy, which is a different act.
            continue
        resolved = dict(group)
        plan_ids = [pid for pid in group.get("plan_ids") or [] if pid in known_plans]
        add_on_ids = [aid for aid in group.get("add_on_ids") or [] if aid in known_add_ons]
        if not plan_ids and not add_on_ids:
            # A tab that lists nothing declared has nothing to render.
            continue
        resolved["plan_ids"] = plan_ids
        resolved["add_on_ids"] = add_on_ids
        seen_group_ids.add(group_id)
        kept.append(resolved)

    if not kept:
        return None

    default_group_id = catalog.get("default_group_id")
    if default_group_id not in seen_group_ids:
        default_group_id = None
    return {"default_group_id": default_group_id, "groups": kept}


def _normalize_subscription_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("subscription_config_file must be an object when contract_required=true")
    config = dict(raw)
    config.setdefault("schema_version", "mozaiks.subscriptions.v1")
    config.setdefault("assignment_store", None)
    config.setdefault("token_wallets", [])
    config.setdefault("add_on_products", [])
    config.setdefault("plans", [])
    config["pricing_catalog"] = _degraded_pricing_catalog(config)
    validated = SubscriptionsConfig.model_validate(config)
    normalized = validated.model_dump(mode="python", exclude_none=True)
    _restore_explicit_nulls(validated, normalized)
    for key in ("token_wallets", "top_up_products", "add_on_products", "usage_charge_policies"):
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
        "add_on_products": list(config.get("add_on_products") or []),
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
        # Without a declared review_status the outcome validator discards the
        # payload as unrecognised and the reason is lost.
        return {
            "success": False,
            "review_status": "blocked",
            "error": "No SubscriptionContractOutput structured output found",
        }

    from factory_app.workflows._shared.platform.build_target import require_build_binding

    binding = require_build_binding(context_variables)
    app_id = binding.target_app_id
    chat_id = _cv_get(context_variables, "chat_id")
    user_id = _cv_get(context_variables, "user_id")
    build_mode = "revision" if binding.phase == "refinement" else "genesis"
    workflow_name = _cv_get(context_variables, "workflow_name") or "SubscriptionContractDesigner"

    if not app_id:
        return {"success": False, "review_status": "blocked", "error": "app_id required in context or output"}

    try:
        normalized = normalize_subscription_contract(output)
    except Exception as exc:
        # The designer can fix a malformed contract; give it the turn back with
        # the validator's message instead of a generic invalid_tool_outcome.
        result = _request_changes(context_variables, str(exc), source="contract_validation")
        return {**result, "error": f"invalid_subscription_contract: {exc}", "details": str(exc)}

    if not bool(normalized.get("contract_required")):
        contradiction = _concept_requires_contract(context_variables)
        if contradiction:
            logger.warning(
                "[SubscriptionContractDesigner] contract_required=false contradicts the approved "
                "concept's monetization_intent for app=%s; returning the turn to the designer",
                app_id,
            )
            return _request_changes(context_variables, contradiction, source="concept_monetization_intent")

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
            raise
        else:
            review_response = dict(response) if isinstance(response, dict) else {"response": response}
            if not _approved_review_response(response):
                result = _request_changes(
                    context_variables, _review_change_request(response), source="review_ui",
                )
                # Keep the user's full response, not only the extracted request.
                _cv_set(context_variables, "subscription_contract_review_response", review_response)
                return result
            review_status = "confirmed"

    normalized["review_status"] = review_status
    normalized["user_confirmed"] = review_status == "confirmed"
    if review_response:
        normalized["review_response"] = review_response

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
            input_artifact_kinds=("concept", "design_docs"),
        )
        _cv_set(context_variables, "subscription_contract_artifact_version_id", artifact.id)
    except Exception as exc:
        logger.warning("[SubscriptionContractDesigner] Artifact persistence failed: %s", exc)
        raise

    _cv_set(context_variables, "subscription_contract", normalized)
    _cv_set(context_variables, "subscription_contract_files", normalized.get("code_files") or [])
    _cv_set(context_variables, "subscription_contract_review_status", review_status)
    if review_response:
        _cv_set(context_variables, "subscription_contract_review_response", review_response)

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
