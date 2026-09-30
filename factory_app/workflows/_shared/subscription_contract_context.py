"""Closed pricing features and derived subscription entitlements.

DesignDocs owns the feature inventory. The designer chooses which approved
features each plan includes; this module names capabilities and action gates.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

import yaml

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    all_module_actions,
    managed_facade_actions,
    ungated_module_actions,
)
from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
    MISSING_FEATURE_SELECTION_MESSAGE,
    capability_id_for_feature,
    features_requiring_gate,
    resolve_subscription_contract,
)

logger = logging.getLogger(__name__)

_TARGET_AGENTS = {
    "AppPlanAgent",
    "AppSchemaAgent",
    "ConfigMiddlewareAgent",
    "PatternAgent",
    "WorkflowBundleBuilderAgent",
}


def _context_value(context_variables: Any, key: str) -> Any:
    if context_variables is None:
        return None
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return detach(getter(key))
        except Exception:
            return None
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return detach(data.get(key))
    return None


def _is_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def concept_requires_contract(context_variables: Any) -> str | None:
    """Explain when the approved concept has already decided a contract is required."""
    if not _is_enabled(_context_value(context_variables, "monetization_enabled")):
        return None
    if str(_context_value(context_variables, "brownfield_build_path") or "").strip():
        # The existing app may already own billing; preserve the brownfield exception.
        return None
    blueprint = _context_value(context_variables, "concept_blueprint")
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


def approved_module_actions(context_variables: Any) -> dict[str, list[str]]:
    """Paid features are approved writes/custom reads, never canonical reads/facades."""
    inventory = all_module_actions(context_variables)
    facades = managed_facade_actions(context_variables)
    ungated = ungated_module_actions(context_variables)
    return {module_id: sorted(set(actions) - set(ungated.get(module_id, [])))
            for module_id, actions in inventory.items() if module_id not in facades}


def approved_feature_inventory(context_variables: Any) -> dict[str, dict[str, str]]:
    """Finite selectable features from approved design and canonical writes."""
    features = {
        f"module.{module_id}.{action_id}": {
            "surface_kind": "module", "module_id": module_id, "action_id": action_id,
        }
        for module_id, actions in approved_module_actions(context_variables).items()
        for action_id in actions
    }
    return dict(sorted(features.items()))


def selected_feature_gates(
    selected_features_by_plan: Mapping[str, list[str]], context_variables: Any,
) -> dict[str, dict[str, str]]:
    """Validate selections and derive module gates without interpreting prose."""
    inventory = approved_feature_inventory(context_variables)
    selected = set().union(*(set(features) for features in selected_features_by_plan.values()))
    unknown = sorted(selected - inventory.keys())
    if unknown:
        raise ValueError(
            f"Plans select features outside the approved inventory: {unknown}. "
            f"Valid features: {sorted(inventory)}. Remove an unavailable feature from the plan "
            "or have DesignDocs approve its action before pricing design."
        )
    gates: dict[str, dict[str, str]] = {}
    for feature_id in sorted(features_requiring_gate(selected_features_by_plan)):
        feature = inventory[feature_id]
        if feature["surface_kind"] == "module":
            gates.setdefault(feature["module_id"], {})[feature["action_id"]] = capability_id_for_feature(feature_id)
    return gates


def validate_module_contract_updates(
    contract: Mapping[str, Any], context_variables: Any,
) -> dict[str, dict[str, str]]:
    """Recheck persisted derived grants and gates against the closed selection."""
    contract = detach(contract)
    if not contract.get("contract_required"):
        return {}
    selected_by_plan = contract.get("selected_features_by_plan")
    if not isinstance(selected_by_plan, Mapping):
        raise ValueError(MISSING_FEATURE_SELECTION_MESSAGE)
    if not all(isinstance(features, list) and all(isinstance(feature, str) for feature in features)
               for features in selected_by_plan.values()):
        raise ValueError("selected_features_by_plan must map plan ids to feature id lists.")
    if any(len(features) != len(set(features)) for features in selected_by_plan.values()):
        raise ValueError("selected_features_by_plan must not repeat a feature within a plan.")
    gates = selected_feature_gates(selected_by_plan, context_variables)
    config = contract.get("subscription_config_file") or {}
    plans = config.get("plans") or []
    plan_ids = {plan.get("plan_id") for plan in plans}
    if set(selected_by_plan) != plan_ids:
        raise ValueError("selected_features_by_plan must contain exactly the declared plan ids.")
    for plan in plans:
        plan_id = plan["plan_id"]
        expected = sorted({capability_id_for_feature(feature) for feature in selected_by_plan[plan_id]})
        if sorted(plan.get("capabilities") or []) != expected:
            raise ValueError(f"Plan {plan_id!r} capabilities differ from its selected features.")
    updates = contract.get("module_contract_updates") or []
    actual: dict[str, dict[str, str]] = {}
    seen_actions: set[tuple[str, str]] = set()
    for update in updates:
        action = (update["module_id"], update["action_id"])
        if action in seen_actions:
            raise ValueError(f"module_contract_updates repeats derived action {action!r}.")
        seen_actions.add(action)
        gate = update.get("entitlement_gate")
        if gate is not None:
            actual.setdefault(update["module_id"], {})[update["action_id"]] = gate
    if actual != gates:
        raise ValueError("module_contract_updates differ from gates derived from selected features.")
    inventory = approved_feature_inventory(context_variables)
    selected = set().union(*(set(features) for features in selected_by_plan.values()))
    expected_workflows = {
        inventory[feature]["surface_id"]: capability_id_for_feature(feature)
        for feature in selected if inventory[feature]["surface_kind"] == "workflow"
    }
    workflow_updates = contract.get("workflow_contract_updates") or []
    actual_workflows = {
        update["design_surface_id"]: update["capability_id"] for update in workflow_updates
    }
    if len(actual_workflows) != len(workflow_updates) or actual_workflows != expected_workflows:
        raise ValueError("workflow_contract_updates differ from selected workflow features.")
    return gates


def _context_data(agent: Any) -> dict[str, Any]:
    """Read the whole context as plain data.

    `.data` was the only path here, and #300 renamed the bridge's backing store
    to `__data` precisely to stop callers reaching past the authority policy. So
    on every live run this returned `{}`, `resolve_subscription_contract` saw nothing, and the
    hook injected no [SUBSCRIPTION CONTRACT CONTEXT] at all -- silently, because
    an empty context is indistinguishable from an app with no contract. The
    prompts that tell agents to read that section have been pointing at nothing.

    `snapshot()` is the bridge's own detached read, the same accessor
    `auto_tool_handler._container_snapshot` uses.
    """
    context = getattr(agent, "context_variables", None) or getattr(agent, "_context_variables", None)
    if context is None:
        return {}
    if isinstance(context, dict):
        return context
    for method_name in ("snapshot", "to_dict"):
        method = getattr(context, method_name, None)
        if callable(method):
            try:
                data = method()
            except Exception:  # pragma: no cover - defensive
                continue
            if isinstance(data, dict):
                return data
    data = getattr(context, "data", None)
    if isinstance(data, dict):
        return data
    return {}


def _trim_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Keep prompt context compact and omit bulky generated file content."""

    allowed = {
        "contract_required",
        "review_status",
        "user_confirmed",
        "rationale",
        "plan_design_rationale",
        "app_name",
        "subscription_config_file",
        "metering_declarations",
        "selected_features_by_plan",
        "module_contract_updates",
        "workflow_contract_updates",
        "page_surface_requirements",
        "app_generator_instructions",
        "validation_notes",
    }
    return {key: contract.get(key) for key in allowed if key in contract}


def _render_contract(contract: Mapping[str, Any]) -> str:
    if not bool(contract.get("contract_required")):
        rationale = str(contract.get("rationale") or "No app-owned SaaS subscription contract is required.").strip()
        return "\n".join(
            [
                "[SUBSCRIPTION CONTRACT CONTEXT]",
                "No app-owned SaaS subscription contract is required for this build.",
                f"Rationale: {rationale}",
                "Do not generate config/subscriptions.yaml, billing modules, token wallets, or metering declarations unless the current task explicitly changes monetization scope.",
            ]
        )

    body = yaml.safe_dump(
        _trim_contract(contract),
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    ).strip()
    return "\n".join(
        [
            "[SUBSCRIPTION CONTRACT CONTEXT]",
            "Use this provider-neutral contract as the source of truth for generated SaaS plans, entitlement gates, token wallets, depleted-balance recovery metadata, top-up products with price.amount_cents/currency, add-on products with price.amount_cents/currency, token allowances, usage pages, and workflow metering declarations.",
            "Do not implement a custom usage ledger or token wallet. Use the OSS runtime subscription/token primitives.",
            "Do not add hosted-product provider behavior here; payment checkout, invoices, and settlement are app-owned or host-provided integration concerns.",
            "Since contract_required is true, AppBuildPlan must include a build task with task_type='subscription_config', capability_pack_id=null, surface_id='subscription_contract', surface_kind='app_policy', initial_agent='ConfigMiddlewareAgent', owned_paths=['config/subscriptions.yaml']. That task serializes this contract's subscription_config_file.",
            "AppGenerator deterministically declares every assignment_store.data_alias, its local assignment collection, and lookup index in data/contract.json from this approved subscription contract, including product stores. This applies to managed and self-hosted writers. Managed billing facades still own no app collections; assignment storage belongs to the subscription_assignments app_policy surface. Do not declare aliases inside migrations or generate provider-owned plans and billing records.",
            "If this contract's subscription_config_file declares assignment_store and the build does not include any managed capability pack that provides the subscription_write_path capability, AppBuildPlan must also include three entitlement_dispatch tasks: (1) one data_migrations task (capability_pack_id: 'entitlement_dispatch', owned_paths: ['data/migrations/001_entitlement_dispatch_collections.json'], initial_agent: 'DatabaseAgent') using the versioned pack template with operations: [] because data/contract.json owns assignment storage, (2) one module_contract task (capability_pack_id: 'entitlement_dispatch', owned_paths: ['modules/entitlement_dispatch/module.yaml'], initial_agent: 'ConfigMiddlewareAgent'), and (3) one business_services task (capability_pack_id: 'entitlement_dispatch', owned_paths: ['modules/entitlement_dispatch/backend/handler.py', 'modules/entitlement_dispatch/backend/service.py', 'modules/entitlement_dispatch/backend/repo.py'], initial_agent: 'ServiceAgent'). Any managed capability pack selected for this build that declares provides_capabilities: [subscription_write_path] in its contract is the write-path owner — entitlement_dispatch must not be included alongside it. The module writes the configured assignment_store.data_alias and is the write-side partner for ConfiguredEntitlementAdapter (the runtime read side). The bundle scanner rejects apps that declare assignment_store without either entitlement_dispatch or a managed assignment writer.",
            body,
        ]
    )


def _apply_text(agent: Any, text: str, *, prepend: bool = False) -> None:
    current = getattr(agent, "_system_message", None) or getattr(agent, "system_message", "") or ""
    updated = (f"{text}\n\n{current}" if prepend else f"{current}\n\n{text}").strip()
    if hasattr(agent, "update_system_message"):
        agent.update_system_message(updated)
    elif hasattr(agent, "_system_message"):
        agent._system_message = updated
    else:
        try:
            agent.system_message = updated
        except Exception:
            return
    agent._mozaiks_base_system_message = updated


def inject_subscription_action_inventory(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Give the designer the finite feature choices its save validator accepts."""
    if getattr(agent, "name", None) != "ContractDesignerAgent":
        return
    data = _context_data(agent)
    inventory = approved_feature_inventory(data)
    rendered = yaml.safe_dump(inventory, sort_keys=True).strip()
    _apply_text(agent, "\n".join([
        "[APPROVED PRICING FEATURE INVENTORY]",
        "Select plans[].included_features only from these exact feature ids.",
        "Module features project approved app-owned writes, canonical writes, and declared custom_reads.",
        "Workflow features cannot be sold until workflow launch enforces subscription grants.",
        "Canonical collection list/get actions and managed-pack facades are excluded and always remain ungated.",
        "A paid view requires a declared custom read; never gate the base collection list/get to sell a dashboard.",
        "Plans, upgrade, checkout, portal, usage, and token access stay ungated.",
        "Code derives capability ids, plan grants, and module gates from the selected features.",
        "A free tier with a limit includes the core feature and assigns a usage limit to it.",
        "If a desired feature is missing, remove it from a plan or have DesignDocs approve its action.",
        rendered,
    ]))
    context = getattr(agent, "context_variables", None) or getattr(agent, "_context_variables", None)
    leading: list[str] = []
    review = data.get("subscription_contract_review_response")
    if isinstance(review, Mapping) and review.get("action") == "request_changes":
        requested_changes = str(review.get("requested_changes") or "").strip()
        leading.append("[REQUIRED CORRECTION]\n" + (requested_changes or "Revise the subscription contract."))
    if concept_requires_contract(context):
        blueprint = data["concept_blueprint"]
        summary = str(blueprint["monetization_intent"].get("money_flow_summary") or "").strip()
        leading.append("\n".join([
            "[CONTRACT DECISION]",
            "The approved concept requires a subscription contract for this build.",
            f"Approved money_flow_summary: {json.dumps(summary, ensure_ascii=False)}",
            "Emit contract_required=true and a subscription_config_file plan design.",
            "The no-op contract is unavailable.",
        ]))
    if leading:
        _apply_text(agent, "\n\n".join(leading), prepend=True)
    logger.info("SUBSCRIPTION_FEATURE_INVENTORY injected features=%d", len(inventory))


def inject_subscription_contract_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Inject provider-neutral subscription contract context into generator agents."""

    agent_name = str(getattr(agent, "name", "") or "").strip()
    if agent_name not in _TARGET_AGENTS:
        return
    data = _context_data(agent)
    contract = resolve_subscription_contract(data)
    if not contract:
        # Say which source was consulted and what was there. This hook read an
        # attribute the live context container does not expose and so injected
        # nothing on every build until #718 -- silently, because it logged
        # neither its success nor its no-op. Two acceptance runs afterwards
        # still could not answer "did it fire?", because agent system messages
        # are not logged either. A hook that never speaks cannot be verified
        # from a live run, only from a test.
        logger.info(
            "SUBSCRIPTION_CONTRACT_CONTEXT skipped agent=%s reason=no_contract "
            "context_keys=%d subscription_contract=%s artifact=%s",
            agent_name,
            len(data),
            "set" if data.get("subscription_contract") is not None else "null",
            "set" if data.get("subscription_contract_artifact") is not None else "null",
        )
        # A cleared contract with a changes_requested review status means the
        # reviewer rejected the last submission and no approved contract exists.
        # Downstream generation must not silently proceed as a non-SaaS build;
        # the SubscriptionContractDesigner revision loop owns re-approval.
        review_status = str(data.get("subscription_contract_review_status") or "").strip()
        if review_status == "changes_requested":
            raise RuntimeError(
                "Subscription contract review requested changes and no approved "
                "contract is available. Re-run SubscriptionContractDesigner to "
                "revise and approve the contract before downstream generation."
            )
        return
    rendered = _render_contract(contract)
    _apply_text(agent, rendered)
    logger.info(
        "SUBSCRIPTION_CONTRACT_CONTEXT injected agent=%s contract_required=%s "
        "plans=%d chars=%d",
        agent_name,
        contract.get("contract_required"),
        len((contract.get("subscription_config_file") or {}).get("plans") or []),
        len(rendered),
    )


__all__ = [
    "approved_feature_inventory",
    "approved_module_actions",
    "capability_id_for_feature",
    "concept_requires_contract",
    "inject_subscription_action_inventory",
    "inject_subscription_contract_context",
    "selected_feature_gates",
    "validate_module_contract_updates",
]
