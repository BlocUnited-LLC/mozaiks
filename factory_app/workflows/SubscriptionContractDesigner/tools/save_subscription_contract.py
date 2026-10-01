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

from factory_app.workflows._shared.subscription_contract_context import (
    approved_feature_inventory,
    capability_id_for_feature,
    concept_requires_contract,
    selected_feature_gates,
    subscription_assignment_store,
    validate_module_contract_updates,
)
from mozaiksai.core.artifacts import persist_summary_artifact
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    approved_workflow_surface_ids,
)
from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool

logger = logging.getLogger(__name__)

_PROPRIETARY_TERMS = (
    "".join(("mozaiks", "pay")),
    "_".join(("hosted", "billing")),
    " ".join(("hosted", "billing")),
    "_".join(("managed", "billing")),
)


def _cv_get(context_variables: Any, key: str) -> Any:
    """Read a context value as plain data.

    Every live container freezes on read, so without detach() this returns a
    MappingProxyType and every `isinstance(..., dict)` on the result is False.
    #709's monetization guard detaches at its own call site for that reason;
    detaching here makes that the default rather than something each new
    reader has to remember.
    """
    if context_variables is None:
        return None
    if hasattr(context_variables, "get"):
        try:
            return detach(context_variables.get(key))
        except Exception:
            return None
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return detach(data.get(key))
    if isinstance(context_variables, dict):
        return detach(context_variables.get(key))
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


def _page_inventory_conflict(output: dict[str, Any], context_variables: Any) -> str | None:
    """Keep greenfield subscription requirements within the approved design."""
    if _cv_get(context_variables, "brownfield_build_path"):
        return None
    experience = _cv_get(context_variables, "experience_spec") or {}
    approved_routes = {page["route"] for page in experience.get("pages") or []}
    if not approved_routes:
        return None
    requested_routes = {page["route"] for page in output.get("page_surface_requirements") or []}
    unapproved = requested_routes - approved_routes
    if not unapproved:
        return None
    return (
        f"page_surface_requirements includes routes outside approved experience_spec.pages: {sorted(unapproved)}. "
        f"Use only approved routes {sorted(approved_routes)} and put subscription requirements on those pages; "
        "do not add a separate page inventory."
    )


def _contains_proprietary_term(value: Any) -> str | None:
    text = yaml.safe_dump(value, sort_keys=False, allow_unicode=False).lower()
    for term in _PROPRIETARY_TERMS:
        if term in text:
            return term
    return None


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
    config.setdefault("token_wallets", [])
    config.setdefault("add_on_products", [])
    config.setdefault("plans", [])
    config["pricing_catalog"] = _degraded_pricing_catalog(config)
    validated = SubscriptionsConfig.model_validate(config)
    normalized = validated.model_dump(mode="python", exclude_none=True)
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


def _compile_feature_selections(
    raw_config: dict[str, Any], context_variables: Any,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Compile model-selected features into runtime plan capability grants."""
    inventory = approved_feature_inventory(context_variables)
    valid_features = sorted(inventory)
    config = dict(raw_config)
    selections: dict[str, list[str]] = {}
    errors: list[str] = []
    plans: list[dict[str, Any]] = []
    for plan in raw_config.get("plans") or []:
        if not isinstance(plan, dict):
            raise ValueError("subscription_config_file.plans entries must be objects.")
        plan_id = str(plan.get("plan_id") or "").strip()
        features = plan.get("included_features")
        if "capabilities" in plan:
            errors.append(f"Plan {plan_id!r} writes capabilities; select included_features instead")
        if not isinstance(features, list) or any(not isinstance(item, str) for item in features):
            errors.append(f"Plan {plan_id!r} must list included_features")
            features = []
        unknown = sorted(set(features) - inventory.keys())
        if unknown:
            errors.append(f"Plan {plan_id!r} selects unavailable features {unknown}")
        if len(features) != len(set(features)):
            errors.append(f"Plan {plan_id!r} repeats a feature")
        selections[plan_id] = list(features)
        compiled = {key: value for key, value in plan.items() if key not in {"included_features", "capabilities"}}
        compiled["capabilities"] = sorted({
            capability_id_for_feature(feature_id) for feature_id in features if feature_id in inventory
        })
        limits: list[dict[str, Any]] = []
        for limit in plan.get("usage_limits") or []:
            if not isinstance(limit, dict):
                raise ValueError(f"Plan {plan_id!r} usage_limits entries must be objects.")
            feature_id = limit.get("feature_id")
            if "capability_id" in limit:
                errors.append(f"Plan {plan_id!r} usage limit writes capability_id; select feature_id instead")
            if feature_id is not None and (not isinstance(feature_id, str) or feature_id not in inventory):
                errors.append(f"Plan {plan_id!r} usage limit references unavailable feature {feature_id!r}")
            elif feature_id is not None and feature_id not in features:
                errors.append(f"Plan {plan_id!r} usage limit references feature {feature_id!r} that it does not include")
            resolved = {key: value for key, value in limit.items() if key not in {"feature_id", "capability_id"}}
            resolved["capability_id"] = capability_id_for_feature(feature_id) if feature_id in features and feature_id in inventory else None
            limits.append(resolved)
        compiled["usage_limits"] = limits
        plans.append(compiled)
    config["plans"] = plans
    add_ons: list[dict[str, Any]] = []
    for add_on in raw_config.get("add_on_products") or []:
        if not isinstance(add_on, dict):
            raise ValueError("subscription_config_file.add_on_products entries must be objects.")
        feature_id = add_on.get("required_feature")
        if "required_capability" in add_on:
            errors.append(f"Add-on {add_on.get('add_on_id')!r} writes required_capability; select required_feature instead")
        if feature_id is not None and (not isinstance(feature_id, str) or feature_id not in inventory):
            errors.append(f"Add-on {add_on.get('add_on_id')!r} references unavailable feature {feature_id!r}")
        compiled = {key: value for key, value in add_on.items() if key not in {"required_feature", "required_capability"}}
        compiled["required_capability"] = capability_id_for_feature(feature_id) if isinstance(feature_id, str) and feature_id in inventory else None
        add_ons.append(compiled)
    config["add_on_products"] = add_ons
    capability_sources: dict[str, str] = {}
    for feature_id in sorted({feature for features in selections.values() for feature in features}):
        if feature_id not in inventory:
            continue
        capability = capability_id_for_feature(feature_id)
        previous = capability_sources.setdefault(capability, feature_id)
        if previous != feature_id:
            errors.append(
                f"Features {previous!r} and {feature_id!r} derive the same capability id {capability!r}"
            )
    if errors:
        raise ValueError(
            "Invalid pricing feature selection: " + "; ".join(errors) + ". "
            f"Valid features: {valid_features}. Remove the unavailable feature reference "
            "from the plan, usage limit, or add-on, or have DesignDocs approve its action "
            "before pricing design."
        )
    return config, selections


def _derive_contract_updates(
    selected_features_by_plan: dict[str, list[str]], output: dict[str, Any], context_variables: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build module gates while keeping workflow metering separate from pricing."""
    inventory = approved_feature_inventory(context_variables)
    gates = selected_feature_gates(selected_features_by_plan, context_variables)
    approved_workflows = set(approved_workflow_surface_ids(context_variables))
    valid_module_actions = sorted({
        (feature["module_id"], feature["action_id"])
        for feature in inventory.values() if feature["surface_kind"] == "module"
    })
    metering: dict[tuple[str, str], dict[str, Any]] = {}
    for declaration in output.get("metering_declarations") or []:
        if not isinstance(declaration, dict):
            raise ValueError(f"Metering declaration {declaration!r} must be an object.")
        if declaration.get("surface_type") == "workflow":
            surface_id = declaration.get("surface_id")
            if surface_id not in approved_workflows or declaration.get("action_id") is not None:
                raise ValueError(
                    f"Workflow metering declaration {declaration!r} references unavailable workflow surface "
                    f"{surface_id!r} "
                    "or specifies an action_id before workflow generation. "
                    f"Valid workflow surfaces: {sorted(approved_workflows)}. "
                    "Remove the declaration or have DesignDocs approve its workflow surface."
                )
            continue
        if declaration.get("surface_type") != "module_action":
            continue
        module_id = declaration.get("surface_id")
        action_id = declaration.get("action_id")
        if (
            not isinstance(module_id, str)
            or not isinstance(action_id, str)
            or (module_id, action_id) not in valid_module_actions
        ):
            raise ValueError(
                f"Metering declaration {declaration!r} must reference an approved module action. "
                f"Valid (surface_id, action_id) pairs: {valid_module_actions}. "
                "Choose one pair or remove the declaration."
            )
        metering[(module_id, action_id)] = declaration
    module_updates = [
        {"module_id": module_id, "action_id": action_id, "entitlement_gate": gate,
         "metering": metering.pop((module_id, action_id), None)}
        for module_id, actions in sorted(gates.items()) for action_id, gate in sorted(actions.items())
    ]
    module_updates.extend(
        {"module_id": module_id, "action_id": action_id, "entitlement_gate": None, "metering": declaration}
        for (module_id, action_id), declaration in sorted(metering.items())
    )
    return module_updates, []


def _metered_surface(declaration: Any) -> str:
    """Name a metering declaration's surface the way a reviewer reads it."""
    if not isinstance(declaration, dict) or not declaration.get("surface_id"):
        return "an unnamed surface"
    surface = str(declaration["surface_id"])
    if declaration.get("action_id"):
        surface = f"{surface}.{declaration['action_id']}"
    return f"the {surface} workflow" if declaration.get("surface_type") == "workflow" else surface


def _normalize_metering_declarations(
    output: dict[str, Any], config: SubscriptionsConfig,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep only metering that can name a declared token wallet."""
    declarations = output.get("metering_declarations") or []
    wallet_ids = sorted(wallet.wallet_id for wallet in config.token_wallets)
    if not wallet_ids:
        notes: list[str] = []
        for declaration in declarations:
            logger.warning(
                "METERING_DECLARATION_DROPPED declaration=%r reason=subscription_config_file.token_wallets is empty",
                declaration,
            )
            # The review UI shows these notes to a person; a dict repr is not a sentence.
            notes.append(
                f"Removed a usage-metering entry for {_metered_surface(declaration)}: this design sells "
                "no token wallets, so there is nothing to charge."
            )
        return [], notes

    normalized: list[dict[str, Any]] = []
    for declaration in declarations:
        if not isinstance(declaration, dict):
            raise ValueError(
                f"Metering declaration {declaration!r} must be an object. "
                f"Declared wallet ids: {wallet_ids}. Remove the declaration."
            )
        resolved = dict(declaration)
        wallet_id = resolved.get("wallet_id")
        if wallet_id not in wallet_ids:
            if len(wallet_ids) != 1:
                raise ValueError(
                    f"Metering declaration {declaration!r} references undeclared wallet_id {wallet_id!r}. "
                    f"Declared wallet ids: {wallet_ids}. Choose one or remove the declaration."
                )
            resolved["wallet_id"] = wallet_ids[0]
            logger.info(
                "METERING_DECLARATION_WALLET_BOUND declaration=%r wallet_id=%s reason=only declared wallet",
                declaration, wallet_ids[0],
            )
        normalized.append(resolved)
    return normalized, []


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
        "selected_features_by_plan": dict(output.get("selected_features_by_plan") or {}),
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


def _normalize_required(output: dict[str, Any], context_variables: Any) -> dict[str, Any]:
    normalized = dict(output)
    raw_config = normalized.get("subscription_config_file")
    if not isinstance(raw_config, dict):
        raise ValueError("subscription_config_file must be an object when contract_required=true")
    if raw_config.get("assignment_store") is not None:
        # The designer's schema no longer has this field; only an output recorded
        # before the store became code-owned still carries one.
        logger.info("ASSIGNMENT_STORE_CONSTRUCTED discarded=%r", raw_config["assignment_store"])
    raw_config = {**raw_config, "assignment_store": subscription_assignment_store()}
    compiled_config, selections = _compile_feature_selections(raw_config, context_variables)
    config = _normalize_subscription_config(compiled_config)
    validated_config = SubscriptionsConfig.model_validate(config)
    normalized["selected_features_by_plan"] = selections
    metering, notes = _normalize_metering_declarations(output, validated_config)
    normalized["metering_declarations"] = metering
    normalized["validation_notes"] = [*list(normalized.get("validation_notes") or []), *notes]
    _validate_token_wallet_scope(normalized, validated_config)
    normalized["module_contract_updates"], normalized["workflow_contract_updates"] = _derive_contract_updates(
        selections, normalized, context_variables,
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


def normalize_subscription_contract(output: dict[str, Any], context_variables: Any = None) -> dict[str, Any]:
    """Normalize and validate a SubscriptionContractOutput dict."""

    forbidden_authored = {
        "selected_features_by_plan", "module_contract_updates", "workflow_contract_updates", "code_files",
    } & output.keys()
    if forbidden_authored:
        raise ValueError(f"Model output must not author derived fields: {sorted(forbidden_authored)}")
    term = _contains_proprietary_term(output)
    if term:
        raise ValueError(f"Subscription contract must be provider-neutral; found proprietary term {term!r}")

    if not bool(output.get("contract_required")):
        return _normalized_noop(output)
    return _normalize_required(output, context_variables)


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

    required_by_concept = concept_requires_contract(context_variables)
    if (
        required_by_concept
        and output.get("contract_required") is False
        and output.get("subscription_config_file") is not None
    ):
        logger.info("CONTRACT_REQUIRED_DETERMINED app=%s", app_id)
        output = {**output, "contract_required": True}

    try:
        normalized = normalize_subscription_contract(output, context_variables)
        validate_module_contract_updates(normalized, context_variables)
        page_conflict = (
            _page_inventory_conflict(normalized, context_variables) if binding.phase == "genesis" else None
        )
    except Exception as exc:
        # The designer can fix a malformed contract; give it the turn back with
        # the validator's message instead of a generic invalid_tool_outcome.
        result = _request_changes(context_variables, str(exc), source="contract_validation")
        return {**result, "error": f"invalid_subscription_contract: {exc}", "details": str(exc)}

    if page_conflict:
        return _request_changes(context_variables, page_conflict, source="approved_page_inventory")

    if not bool(normalized.get("contract_required")):
        if required_by_concept:
            logger.warning(
                "[SubscriptionContractDesigner] contract_required=false contradicts the approved "
                "concept's monetization_intent for app=%s; returning the turn to the designer",
                app_id,
            )
            return _request_changes(
                context_variables, required_by_concept, source="concept_monetization_intent",
            )

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
