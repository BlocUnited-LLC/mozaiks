"""Inject a compact implementation catalog for grounded product recommendations.

Catalog identifiers guide feasibility internally; interview messages describe
user-visible behavior. Binding decisions remain with the app planning stage.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import yaml

from factory_app.workflows._shared.hook_utils import update_agent_section, workflow_context_path

logger = logging.getLogger(__name__)

_MENU_HEADER = "[BUILDABLE MENU]"
_TARGET_AGENT = "ValueInterviewAgent"

_ROUTING_PATH = workflow_context_path("AppGenerator", "capability_routing.yaml")
_DOMAINS_PATH = workflow_context_path("AppGenerator", "domain_catalogs.yaml")
_ARCHETYPES_PATH = workflow_context_path("AppGenerator", "module_archetypes.yaml")

# Carry capability selection boundaries directly from the canonical catalog.
_MAX_DOMAINS = 40
_MAX_MODULES_PER_DOMAIN = 6


@lru_cache(maxsize=3)
def _load_yaml(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        if not isinstance(data, dict):
            logger.warning("%s did not parse as a mapping — ignoring", path)
            return None
        return data
    except Exception as exc:
        logger.warning("%s could not be loaded: %s", path, exc)
        return None


def _first_line(value: Any) -> str:
    text = str(value or "").strip()
    return text.split("\n")[0].strip() if text else ""


def _format_packs(routing: dict[str, Any]) -> str:
    layers = routing.get("layers") or {}
    packs = [p for p in ((layers.get("capability_pack") or {}).get("packs") or []) if isinstance(p, dict)]
    if not packs:
        return ""
    lines = [
        "CAPABILITY PACKS — internal choices for implementing feature areas.",
        "Explain their user-visible behavior without exposing pack identifiers.",
    ]
    for pack in packs:
        pack_id = _first_line(pack.get("id"))
        if not pack_id:
            continue
        covers = _first_line(pack.get("covers"))
        line = f"  - {pack_id}: {covers}" if covers else f"  - {pack_id}"
        use_when = _first_line(pack.get("use_when"))
        avoid_when = _first_line(pack.get("avoid_when"))
        requires = pack.get("requires")
        if use_when:
            line += f"\n      offer when: {use_when}"
        if avoid_when:
            line += f"\n      do not offer when: {avoid_when}"
        if isinstance(requires, list) and requires:
            line += f"\n      requires: {', '.join(str(r) for r in requires)}"
        lines.append(line)
    return "\n".join(lines)


def _format_archetypes(archetypes_doc: dict[str, Any]) -> str:
    archetypes = archetypes_doc.get("archetypes")
    if not isinstance(archetypes, dict) or not archetypes:
        return ""
    lines = [
        "MODULE ARCHETYPES — internal implementation shapes, not user-facing labels.",
        "The workflow archetype is deterministic state transitions, not an AI",
        "workflow. Describe the product behavior without exposing these names.",
    ]
    for name, spec in archetypes.items():
        if not isinstance(spec, dict):
            continue
        summary = _first_line(spec.get("description") or spec.get("summary") or spec.get("use_when"))
        lines.append(f"  - {name}: {summary}" if summary else f"  - {name}")
    return "\n".join(lines)


def _format_domains(domains_doc: dict[str, Any]) -> str:
    domains = domains_doc.get("domains")
    if not isinstance(domains, dict) or not domains:
        return ""
    lines = [
        "DOMAIN PRIORS — typical modules per product domain. Use these to infer what",
        "an idea implies instead of asking the user to enumerate features.",
    ]
    for index, (domain, spec) in enumerate(domains.items()):
        if index >= _MAX_DOMAINS or not isinstance(spec, dict):
            continue
        modules = spec.get("modules")
        names: list[str] = []
        if isinstance(modules, list):
            for module in modules[:_MAX_MODULES_PER_DOMAIN]:
                if isinstance(module, dict):
                    module_name = _first_line(module.get("name") or module.get("id"))
                elif isinstance(module, str):
                    module_name = module.strip()
                else:
                    module_name = ""
                if module_name:
                    names.append(module_name)
        packs = spec.get("capability_packs")
        line = f"  - {domain}: {', '.join(names)}" if names else f"  - {domain}"
        if isinstance(packs, list) and packs:
            line += f"  [packs: {', '.join(str(p) for p in packs)}]"
        lines.append(line)
    return "\n".join(lines)


def _format_revenue(routing: dict[str, Any]) -> str:
    layers = routing.get("layers") or {}
    monetization = layers.get("monetization")
    if not isinstance(monetization, dict):
        return ""
    models = monetization.get("revenue_models")
    lines = [
        "REVENUE MODELS — pick exactly one when the user wants to charge.",
        '"Monetized" is not a route; resolve it to one of these before proposing files.',
    ]
    if isinstance(models, dict):
        for model, spec in models.items():
            if not isinstance(spec, dict):
                lines.append(f"  - {model}")
                continue
            pack = _first_line(spec.get("capability_pack"))
            contract = _first_line(spec.get("subscription_contract"))
            detail = " / ".join(part for part in (pack, contract) if part)
            lines.append(f"  - {model}: {detail}" if detail else f"  - {model}")
    else:
        lines.append("  - free | subscriptions | usage_based | custom | hybrid")
    lines.append(
        "  Subscription pieces you can offer concretely: plans with capabilities,\n"
        "  usage meters (tokens | requests | credits), token wallets with monthly\n"
        "  allowances, one-off top-up products, and add-ons."
    )
    return "\n".join(lines)


_GUARDRAILS = """PROMISE DISCIPLINE — an advisor that over-promises is worse than one that asks
dull questions, because the build then fails on a commitment you made.
  - Only name packs, archetypes, domains and revenue models listed above.
  - In-app notifications are provided by the runtime; never offer a
    "notifications module" for them.
  - Identity, login, accounts and profiles are platform-provided; never offer to
    rebuild them.
  - Hosting/deployment (mozaiks_cloud) is never assumed from "deploy it" — it
    needs the user to ask for it explicitly.
  - Payment provider mechanics are never generated into an app.
  - If the right answer is not on this menu, describe the behavior in plain
    language instead of inventing a component name."""


def _build_menu(routing: dict[str, Any] | None,
                domains_doc: dict[str, Any] | None,
                archetypes_doc: dict[str, Any] | None) -> str:
    blocks: list[str] = [
        "Use this implementation catalog to ground product recommendations.\n"
        "Keep its identifiers internal; preserve the user's requested behavior.",
    ]
    if routing:
        packs = _format_packs(routing)
        if packs:
            blocks.append(packs)
    if archetypes_doc:
        archetypes = _format_archetypes(archetypes_doc)
        if archetypes:
            blocks.append(archetypes)
    if domains_doc:
        domain_block = _format_domains(domains_doc)
        if domain_block:
            blocks.append(domain_block)
    if routing:
        revenue = _format_revenue(routing)
        if revenue:
            blocks.append(revenue)
    blocks.append(_GUARDRAILS)
    return "\n\n".join(blocks)


def inject_buildable_menu_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Prompt middleware for ValueInterviewAgent: inject [BUILDABLE MENU].

    Fires on every turn so the menu is in scope whenever a proposal is made.
    Failure is non-fatal — the agent keeps its prompt and degrades to its
    non-catalog behavior rather than breaking the conversation.
    """

    agent_name = getattr(agent, "name", "")
    if agent_name != _TARGET_AGENT:
        return

    try:
        routing = _load_yaml(_ROUTING_PATH)
        domains_doc = _load_yaml(_DOMAINS_PATH)
        archetypes_doc = _load_yaml(_ARCHETYPES_PATH)
        if not any((routing, domains_doc, archetypes_doc)):
            logger.warning("[%s] No buildable catalogs loaded — menu not injected", agent_name)
            return

        update_agent_section(agent, _MENU_HEADER, _build_menu(routing, domains_doc, archetypes_doc))
        logger.info("[%s] Injected buildable menu context", agent_name)
    except Exception as exc:
        logger.error("[%s] Failed to inject buildable menu: %s", agent_name, exc)


__all__ = ["inject_buildable_menu_context"]
