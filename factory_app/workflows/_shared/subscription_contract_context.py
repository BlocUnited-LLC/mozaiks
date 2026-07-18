"""Prompt injection for generated-app subscription/token contracts.

The SubscriptionContractDesigner workflow persists a provider-neutral contract
artifact. AppGenerator and AgentGenerator consume that contract as context; this
hook only renders the already-produced contract. It does not infer whether an
app should be monetized.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

_TARGET_AGENTS = {
    "AppPlanAgent",
    "AppSchemaAgent",
    "ConfigMiddlewareAgent",
    "PatternAgent",
    "WorkflowBundleBuilderAgent",
}


def _context_data(agent: Any) -> dict[str, Any]:
    context = getattr(agent, "context_variables", None) or getattr(agent, "_context_variables", None)
    if context is None:
        return {}
    data = getattr(context, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(context, dict):
        return context
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
            "Use this provider-neutral contract as the source of truth for generated SaaS plans, entitlement gates, token wallets, depleted-balance recovery metadata, top-up products with price.amount_cents/currency, token allowances, usage pages, and workflow metering declarations.",
            "Do not implement a custom usage ledger or token wallet. Use the OSS runtime subscription/token primitives.",
            "Do not add hosted-product provider behavior here; payment checkout, invoices, and settlement are app-owned or host-provided integration concerns.",
            "If this contract's subscription_config_file declares assignment_store and the app does not use the mozaikspay managed_capability, AppPlanAgent must include an entitlement_dispatch module task set (module_contract + business_services). That module is the write-side partner for ConfiguredEntitlementAdapter (the runtime read side). The bundle scanner rejects apps that declare assignment_store without this module.",
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


def inject_subscription_contract_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Inject provider-neutral subscription contract context into generator agents."""

    agent_name = str(getattr(agent, "name", "") or "").strip()
    if agent_name not in _TARGET_AGENTS:
        return
    contract = _find_contract(_context_data(agent))
    if not contract:
        return
    _apply_text(agent, _render_contract(contract))


__all__ = ["inject_subscription_contract_context"]
