"""Subscription contract context and approved action reference closure.

The SubscriptionContractDesigner workflow persists a provider-neutral contract
artifact. The designer selects gates from approved DesignDocs actions;
AppGenerator and AgentGenerator consume that contract as context. These helpers
validate and project decisions, without deciding which actions should be paid.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import yaml

from mozaiksai.core.workflow.context.frozen import detach

logger = logging.getLogger(__name__)

_TARGET_AGENTS = {
    "AppPlanAgent",
    "AppSchemaAgent",
    "ConfigMiddlewareAgent",
    "PatternAgent",
    "WorkflowBundleBuilderAgent",
}


def approved_module_actions(context_variables: Any) -> dict[str, list[str]]:
    """Project the approved DesignDocs module/action identifiers, without aliases."""
    surface_map = detach(context_variables.get("design_surface_map")) if context_variables is not None else None
    if not isinstance(surface_map, Mapping):
        return {}
    inventory: dict[str, list[str]] = {}
    for surface in surface_map.get("surfaces") or []:
        if surface.get("surface_kind") != "module" or surface.get("owner") != "app":
            continue
        surface_id = surface["surface_id"]
        inventory[surface_id] = sorted(set(surface.get("owned_mutations") or []))
    return inventory


def validate_module_contract_updates(
    contract: Mapping[str, Any], context_variables: Any,
) -> dict[str, dict[str, str]]:
    """Close model-owned gate decisions over approved actions and plan grants.

    No action is selected here: choosing which feature an action implements is
    a product decision. Once references close, downstream materialization can
    apply the returned gates without interpreting a generated module file.
    """
    contract = detach(contract)
    if not contract.get("contract_required"):
        return {}
    inventory = approved_module_actions(context_variables)
    valid_ids = f"Valid approved module/action ids: {inventory!r}."
    config = contract.get("subscription_config_file") or {}
    plan_groups = [config.get("plans") or []]
    plan_groups.extend(product.get("plans") or [] for product in config.get("products") or [])
    capabilities: set[str] = set()
    differing: set[str] = set()
    for plans in plan_groups:
        plan_capabilities = [set(plan.get("capabilities") or []) for plan in plans]
        grants = set().union(*plan_capabilities)
        common = set.intersection(*plan_capabilities) if plan_capabilities else set()
        capabilities.update(grants)
        differing.update(grants - common)
    gates: dict[str, dict[str, str]] = {}
    decisions: dict[tuple[str, str], str | None] = {}
    for update in contract.get("module_contract_updates") or []:
        module_id = update.get("module_id")
        action_id = update.get("action_id")
        gate = update.get("entitlement_gate")
        if module_id not in inventory or action_id not in inventory[module_id]:
            raise ValueError(
                f"module_contract_updates references unapproved action {module_id!r}.{action_id!r}. "
                f"Choose module_id from design_surface_map.surfaces[].surface_id and action_id "
                f"from that module's owned_mutations. {valid_ids}"
            )
        if gate is not None and gate not in capabilities:
            raise ValueError(
                f"module_contract_updates entitlement_gate {gate!r} is not granted by any plan. "
                f"Valid capability ids: {sorted(capabilities)!r}. {valid_ids}"
            )
        action = (module_id, action_id)
        if action in decisions and decisions[action] != gate:
            raise ValueError(
                f"Conflicting entitlement_gate decisions for {module_id}.{action_id}: "
                f"{decisions[action]!r} and {gate!r}. Choose one capability for this action. "
                f"Valid capability ids: {sorted(capabilities)!r}. {valid_ids}"
            )
        decisions[action] = gate
        if gate is not None:
            gates.setdefault(module_id, {})[action_id] = gate
    mapped = {gate for actions in gates.values() for gate in actions.values()}
    missing = differing - mapped
    if missing:
        raise ValueError(
            f"module_contract_updates must map every capability that differs between plans "
            f"to at least one approved action. Unmapped capability ids: {sorted(missing)!r}. "
            f"Choose the product mapping; the materializer cannot infer it. {valid_ids}"
        )
    if capabilities and not mapped:
        raise ValueError(
            f"Plans grant capabilities {sorted(capabilities)!r} but module_contract_updates "
            f"does not select any action entitlement_gate. Choose at least one approved action. {valid_ids}"
        )
    return gates


def _context_data(agent: Any) -> dict[str, Any]:
    """Read the whole context as plain data.

    `.data` was the only path here, and #300 renamed the bridge's backing store
    to `__data` precisely to stop callers reaching past the authority policy. So
    on every live run this returned `{}`, `_find_contract` saw nothing, and the
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


def _extract_summary_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if "contract_required" in value:
        return dict(value)

    commit_metadata = value.get("commit_metadata")
    if isinstance(commit_metadata, Mapping):
        metadata = commit_metadata.get("metadata")
        if isinstance(metadata, Mapping) and isinstance(metadata.get("summary_payload"), Mapping):
            return dict(metadata["summary_payload"])

    metadata = value.get("metadata")
    if isinstance(metadata, Mapping) and isinstance(metadata.get("summary_payload"), Mapping):
        return dict(metadata["summary_payload"])

    return None


def _find_contract(data: Mapping[str, Any]) -> dict[str, Any] | None:
    for key in ("subscription_contract", "subscription_contract_artifact"):
        payload = _extract_summary_payload(data.get(key))
        if payload is not None:
            return payload
    return None


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
            "If this contract's subscription_config_file declares assignment_store and the build does not include any managed capability pack that provides the subscription_write_path capability, AppBuildPlan must also include three entitlement_dispatch tasks: (1) one data_migrations task (capability_pack_id: 'entitlement_dispatch', owned_paths: ['data/migrations/001_entitlement_dispatch_collections.json'], initial_agent: 'DatabaseAgent') that declares the billing.subscriptions data alias, (2) one module_contract task (capability_pack_id: 'entitlement_dispatch', owned_paths: ['modules/entitlement_dispatch/module.yaml'], initial_agent: 'ConfigMiddlewareAgent'), and (3) one business_services task (capability_pack_id: 'entitlement_dispatch', owned_paths: ['modules/entitlement_dispatch/backend/handler.py', 'modules/entitlement_dispatch/backend/service.py', 'modules/entitlement_dispatch/backend/repo.py'], initial_agent: 'ServiceAgent'). Any managed capability pack selected for this build that declares provides_capabilities: [subscription_write_path] in its contract is the write-path owner — entitlement_dispatch must not be included alongside it. The data_migrations task must run before the business_services task because repo.py reads from the billing.subscriptions alias declared by that migration. That module is the write-side partner for ConfiguredEntitlementAdapter (the runtime read side). The bundle scanner rejects apps that declare assignment_store without either entitlement_dispatch or a managed assignment writer.",
            body,
        ]
    )


def _apply_text(agent: Any, text: str) -> None:
    current = getattr(agent, "_system_message", None) or getattr(agent, "system_message", "") or ""
    updated = f"{current}\n\n{text}".strip()
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
    """Give the designer the finite action choices its save validator accepts."""
    if getattr(agent, "name", None) != "ContractDesignerAgent":
        return
    inventory = approved_module_actions(_context_data(agent))
    rendered = yaml.safe_dump(inventory, sort_keys=True).strip()
    _apply_text(agent, "\n".join([
        "[APPROVED ENTITLEMENT ACTION INVENTORY]",
        "These are the only permitted module_id -> action_id choices for module_contract_updates.",
        "They project app-owned module surface_id and owned_mutations from approved design_surface_map.",
        "Copy identifiers exactly. Select entitlement_gate from your plans' capabilities.",
        "Map every capability that differs between plans to at least one action. If any plan grants",
        "capabilities, select at least one gate even when all plans grant the same capabilities.",
        "Each action has at most one entitlement_gate. AppGenerator writes selected gates deterministically.",
        "If no approved action represents a required capability, report the missing design input",
        "in validation_notes; do not invent an action or silently leave the capability unmapped.",
        rendered,
    ]))
    logger.info("SUBSCRIPTION_ACTION_INVENTORY injected modules=%d actions=%d",
                len(inventory), sum(len(actions) for actions in inventory.values()))


def inject_subscription_contract_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Inject provider-neutral subscription contract context into generator agents."""

    agent_name = str(getattr(agent, "name", "") or "").strip()
    if agent_name not in _TARGET_AGENTS:
        return
    data = _context_data(agent)
    contract = _find_contract(data)
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
    "approved_module_actions",
    "inject_subscription_action_inventory",
    "inject_subscription_contract_context",
    "validate_module_contract_updates",
]
