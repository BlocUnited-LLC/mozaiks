"""
Hook: Inject Managed Capabilities Context

Fires as an prompt middleware function on AppPlanAgent.

When ``capability_packs`` is present and non-empty in context_variables
(populated by an operator at launch), this hook injects a compact
[MANAGED CAPABILITIES CONTEXT] block into the AppPlanAgent system message.

The existing header is retained for workflow integration. The body describes
the supplied pack origins and declared output ownership; a generated_module
pack can supply deterministic templates just as a managed pack can.
When capability_packs is null or empty, this hook is a complete no-op.
"""

from __future__ import annotations

import logging
from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section
from mozaiksai.core.workflow.context.frozen import detach

logger = logging.getLogger(__name__)

_MANAGED_CAP_HEADER = "[MANAGED CAPABILITIES CONTEXT]"

# ---------------------------------------------------------------------------
# Guidance for the existing AppCapabilityPack capability_source vocabulary.
# ---------------------------------------------------------------------------

_CAPABILITY_SOURCE_GUIDANCE = {
    "config_file": "Registered pack supplying declarative app configuration; follow its declared outputs.",
    "external_adapter": "Third-party service; author only declared adapter/client wiring, not its backend.",
    "framework_pack": "Registered framework pack; author only declared app-specific wiring, not pack internals.",
    "generated_module": (
        "App-owned module. Preserve complete module/action contracts; author only outputs not supplied "
        "by selected pack templates. This origin does not require regenerating a pack's backend."
    ),
    "host_universal": (
        "Runtime-provided feature; no generated module or scaffold. Reference it in service_scope "
        "or external_integrations, not as a selected capability pack."
    ),
    "managed_capability": "Hosted provider; retain its origin and use its declared app-owned facade boundary.",
    "operator_extension": "Registered operator extension; preserve its declared origin and output contract.",
    "operator_pack": "Registered operator build-context pack; preserve its declared origin and output contract.",
}

_OUTPUT_OWNERSHIP_GUIDANCE = """\
Output ownership applies to every capability_source:
- owner: templates — deterministic assembly supplies the pack's files; exclusive pack outputs are never model work.
- owner: workspace — when the pack ships a seed, assembly supplies it at genesis; preserve the admitted workspace copy on revision. Changes use supported scoped refinement.
- owner: generator — plan app-owned work for the exact declared path and instructions.
- Shared composition roots retain app task ownership; assembly merges the declared pack contributions.
Preserve the listed ids, origins, selection_rules, runtime_boundaries, facade ids, adapter paths and page routes. Source classification does not override file ownership; do not invent alternate contracts."""

_MANAGED_PLANNING_GUIDANCE = """\
Planning rules for managed_capability entries only:
- Include each selected managed capability exactly once with capability_source: managed_capability, implementation_mode: external_integration and the declared surface_kind. Never generate modules/{capability_pack_id}/ for the hosted provider or reclassify it as generated_module.
- For each selected active surface, follow its operator contract. If templates do not already supply the adapter, generate one api_surface task at ControllerAgent for services/integrations/{pack_id}_client.py with capability_pack_id set to the provider id.
- App-owned facade module_contract tasks start at ConfigMiddlewareAgent and depend on the adapter when needed. Plan data_models, business_services and page_bundle work only for outputs not supplied by templates, with their declared dependencies.
- Pages bind to declared app-owned facade module/action pairs, never directly to managed backing modules such as wallet, managed_billing, managed_usage or managed_entitlements. Code renders endpoint URLs; do not invent adapter paths, facade ids or page routes."""


def _capability_source(pack: Any) -> str:
    if isinstance(pack, dict):
        return str(pack.get("capability_source") or "managed_capability").strip()
    return "managed_capability"


def _is_empty(value: Any) -> bool:
    """Return True if value is None, empty list, or empty dict."""
    if value is None:
        return True
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _format_managed_capabilities(packs: list[Any]) -> str:
    lines = [
        "Registered capability packs supplied to planning:",
        "Use the exact listed capability_pack_id and capability_source in AppBuildPlan; follow approved scope and each pack's selection rules.",
    ]
    for pack in packs:
        if isinstance(pack, dict):
            pack_id = pack.get("id") or pack.get("pack_id") or str(pack)
            label = pack.get("display_name") or pack.get("label") or pack_id
            description = pack.get("description") or ""
            caps = pack.get("capabilities") or []
            cap_source = _capability_source(pack)
            line = (
                f"  - {pack_id} ({label}) "
                f"[capability_pack_id: {pack_id}; capability_source: {cap_source}]"
            )
            if description:
                line += f": {description.strip().split(chr(10))[0]}"
            if caps:
                cap_ids = [
                    (c.get("capability_id") or c) if isinstance(c, dict) else str(c)
                    for c in caps[:4]
                ]
                line += f" | capabilities: {', '.join(cap_ids)}"  # type: ignore[arg-type]
            lines.append(line)
        else:
            lines.append(f"  - {pack}")
    return "\n".join(lines)


def _format_pack_surfaces(packs: list[Any]) -> str | None:
    """Render surface groupings and facade hints.

    Accepts two data shapes:

    1. ``surfaces`` list (declarative surface schema):
       Each entry may carry ``generation_hint.facade_module_id`` and
       ``generation_hint.pages`` / ``generation_hint.page_routes``.

    2. ``facades`` list (contract.yaml facade schema):
       Each entry has ``module_id``, ``provider_module``, and ``pages``.
       Rendered as facade identities and bindings. Declared output ownership
       determines whether their implementation needs a model task.

    Returns None when neither key is declared on any pack.
    """
    surface_lines: list[str] = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        pack_id = pack.get("id") or pack.get("pack_id") or "?"
        label = pack.get("display_name") or pack.get("label") or pack_id

        # Shape 1: explicit surfaces list with optional generation_hint
        surfaces = pack.get("surfaces") or []
        if surfaces:
            surface_lines.append(f"{pack_id} ({label}) surfaces:")
            for surface in surfaces:
                if not isinstance(surface, dict):
                    continue
                surface_id = surface.get("surface_id") or "?"
                surface_label = surface.get("label") or surface_id
                status = surface.get("status") or "unknown"
                hint = surface.get("generation_hint") or {}
                facade_module_id = hint.get("facade_module_id") or ""
                pages = hint.get("pages") or []
                page_routes = hint.get("page_routes") or {}
                line = f"  - {surface_id} ({surface_label}) [{status}]"
                if facade_module_id:
                    line += f" → facade_module: {facade_module_id}"
                if pages:
                    line += f" | pages: {', '.join(str(p) for p in pages)}"
                surface_lines.append(line)
                if page_routes and isinstance(page_routes, dict):
                    for page_name, route in page_routes.items():
                        surface_lines.append(f"      route: {page_name} → {route}")

        # Shape 2: facades list from contract.yaml build_provider_values output
        facades = pack.get("facades") or []
        if facades and not surfaces:
            surface_lines.append(f"{pack_id} ({label}) facade modules:")
            for facade in facades:
                if not isinstance(facade, dict):
                    continue
                module_id = facade.get("module_id") or "?"
                provider = facade.get("provider_module") or pack_id
                pages = facade.get("pages") or []
                actions = facade.get("provider_actions") or []
                line = (
                    f"  - Facade module '{module_id}' wraps {provider}; follow its declared output ownership."
                )
                if pages:
                    page_names = [
                        p.get("name") if isinstance(p, dict) else str(p)
                        for p in pages
                    ]
                    routes = [
                        p.get("route") if isinstance(p, dict) else ""
                        for p in pages
                    ]
                    line += f" | pages: {', '.join(str(n) for n in page_names)}"
                    line += f" | routes: {', '.join(str(r) for r in routes if r)}"
                if actions:
                    line += f" | provider_actions: {', '.join(str(a) for a in actions[:4])}"
                surface_lines.append(line)

    if not surface_lines:
        return None
    return "Pack surfaces:\n" + "\n".join(surface_lines)


def _format_pack_supported_domains(packs: list[Any]) -> str | None:
    """Render domain fit hints. Returns None when no domains are declared."""
    domain_lines: list[str] = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        domains = pack.get("supported_domains") or []
        if not domains:
            continue
        pack_id = pack.get("id") or pack.get("pack_id") or "?"
        label = pack.get("display_name") or pack.get("label") or pack_id
        domain_lines.append(f"{pack_id} ({label}) domain fit:")
        for domain_entry in domains:
            if not isinstance(domain_entry, dict):
                continue
            domain = domain_entry.get("domain") or "?"
            fit = domain_entry.get("fit") or "?"
            surfaces = domain_entry.get("surfaces") or []
            blocked = domain_entry.get("blocked_surfaces") or []
            line = f"  - {domain}: fit={fit}"
            if surfaces:
                line += f" | surfaces: {', '.join(str(s) for s in surfaces)}"
            if blocked:
                line += f" | blocked: {', '.join(str(s) for s in blocked)}"
            domain_lines.append(line)
    if not domain_lines:
        return None
    return "Pack domain fit:\n" + "\n".join(domain_lines)


def _format_pack_branding(packs: list[Any]) -> str | None:
    """Render branding hints. Returns None when no packs declare branding."""
    branding_lines: list[str] = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        branding = pack.get("branding") or {}
        if not branding:
            continue
        pack_id = pack.get("id") or pack.get("pack_id") or "?"
        label = pack.get("display_name") or pack.get("label") or pack_id
        branding_lines.append(f"{pack_id} ({label}) branding:")
        attribution = branding.get("attribution") or ""
        if attribution:
            branding_lines.append(f"  - attribution: {attribution}")
        app_branded = branding.get("app_branded_surfaces") or []
        if app_branded:
            branding_lines.append(
                f"  - app-branded surfaces: {', '.join(str(s) for s in app_branded)}"
            )
        co_branded = branding.get("co_branded_surfaces") or []
        if co_branded:
            branding_lines.append(
                f"  - co-branded surfaces: {', '.join(str(s) for s in co_branded)}"
            )
        redirect_surfaces = branding.get("hosted_redirect_surfaces") or []
        if redirect_surfaces:
            branding_lines.append(
                f"  - hosted redirect surfaces: {', '.join(str(s) for s in redirect_surfaces)}"
            )
    if not branding_lines:
        return None
    return "Pack branding:\n" + "\n".join(branding_lines)


def _format_contract_list_items(items: Any, *, key: str) -> list[str]:
    lines: list[str] = []
    if not isinstance(items, list):
        return lines
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get(key)
        if value:
            lines.append(f"  - {value}")
    return lines


def _format_required_outputs(items: Any) -> list[str]:
    """Keep each declared path's ownership and app-authoring instructions visible."""
    lines: list[str] = []
    if not isinstance(items, list):
        return lines
    for item in items:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        owner = str(item.get("owner") or "templates").strip()
        lines.append(f"  - {item['path']} [owner: {owner}]")
        notes = str(item.get("notes") or "").strip()
        if notes:
            lines.extend(f"      {line}" for line in ("notes: " + notes).splitlines())
    return lines


def _format_selection_rules(items: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(items, list):
        return lines
    for item in items:
        if not isinstance(item, dict):
            continue
        rule_id = item.get("id") or "rule"
        action = item.get("action") or ""
        when = item.get("when") if isinstance(item.get("when"), dict) else {}
        suffix = f" -> {action}" if action else ""
        conditions: list[str] = []
        intents = when.get("intent_any") if isinstance(when, dict) else []
        if isinstance(intents, list) and intents:
            conditions.append("intent_any: " + ", ".join(str(i) for i in intents))
        declares = when.get("app_declares") if isinstance(when, dict) else []
        if isinstance(declares, list) and declares:
            conditions.append("app_declares: " + ", ".join(str(d) for d in declares))
        condition_text = "; ".join(conditions)
        lines.append(f"  - {rule_id}{suffix}" + (f" when {condition_text}" if condition_text else ""))
    return lines


def _format_facade_contracts(facades: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(facades, list):
        return lines
    for facade in facades:
        if not isinstance(facade, dict):
            continue
        module_id = facade.get("module_id") or facade.get("facade_id")
        provider_module = facade.get("provider_module")
        if module_id:
            line = f"  - {module_id}"
            if provider_module:
                line += f" wraps {provider_module}"
            lines.append(line)
        for page in facade.get("pages") or []:
            if isinstance(page, dict) and page.get("route"):
                actions = page.get("primary_actions") or []
                action_text = f" actions: {', '.join(str(action) for action in actions)}" if actions else ""
                lines.append(f"      page {page.get('route')}{action_text}")
    return lines


def _format_operator_contracts(contracts: list[Any]) -> str | None:
    blocks: list[str] = []
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        contract_id = contract.get("contract_id") or "operator_contract"
        lines = [f"{contract_id} ({contract.get('contract_type') or 'contract'}):"]
        selection = _format_selection_rules(contract.get("selection_rules"))
        if selection:
            lines.append("  selection_rules:")
            lines.extend(selection)
        required = _format_required_outputs(contract.get("required_outputs"))
        if required:
            lines.append("  required_outputs:")
            lines.extend(required)
        forbidden = _format_contract_list_items(contract.get("forbidden_outputs"), key="path_prefix")
        if forbidden:
            lines.append("  forbidden_outputs:")
            lines.extend(forbidden)
        boundaries = _format_contract_list_items(contract.get("runtime_boundaries"), key="rule")
        if boundaries:
            lines.append("  runtime_boundaries:")
            lines.extend(boundaries)
        facades = _format_facade_contracts(contract.get("facades"))
        if facades:
            lines.append("  facades:")
            lines.extend(facades)
        inactive = contract.get("inactive_surfaces") or []
        if isinstance(inactive, list) and inactive:
            lines.append("  inactive_surfaces:")
            lines.extend(f"  - {surface}" for surface in inactive)
        blocks.append("\n".join(lines))
    if not blocks:
        return None
    return "Operator build-pack contracts:\n" + "\n".join(blocks)


def _build_managed_context_body(capability_packs: list[Any], operator_contracts: list[Any] | None = None) -> str:
    sources = {_capability_source(pack) for pack in capability_packs}
    source_guidance = "\n".join(
        f"  {source} — {guidance}" for source, guidance in _CAPABILITY_SOURCE_GUIDANCE.items() if source in sources
    )
    parts = [
        _format_managed_capabilities(capability_packs),
        "Capability source guidance:\n" + source_guidance,
        _OUTPUT_OWNERSHIP_GUIDANCE,
    ]
    if "managed_capability" in sources:
        parts.append(_MANAGED_PLANNING_GUIDANCE)

    surfaces_block = _format_pack_surfaces(capability_packs)
    if surfaces_block is not None:
        parts.append(surfaces_block)

    domains_block = _format_pack_supported_domains(capability_packs)
    if domains_block is not None:
        parts.append(domains_block)

    branding_block = _format_pack_branding(capability_packs)
    if branding_block is not None:
        parts.append(branding_block)

    contracts_block = _format_operator_contracts(operator_contracts or [])
    if contracts_block is not None:
        parts.append(contracts_block)

    return "\n\n".join(parts)


def inject_managed_capabilities_context(
    agent: Any,
    messages: list[dict[str, Any]],
) -> None:
    """
    prompt middleware function for AppPlanAgent.

    Reads ``capability_packs`` from context_variables. No-ops when the list is
    null or empty (OSS mode). Injects [MANAGED CAPABILITIES CONTEXT] when packs
    are present.
    """
    agent_name = getattr(agent, "name", "")
    if agent_name != "AppPlanAgent":
        return

    try:
        context_variables: Any = getattr(agent, "context_variables", None)
        if context_variables is None:
            return
        # Live containers freeze reads (tuples and mapping proxies); every
        # formatter below type-tests plain lists and dicts.
        capability_packs = detach(context_variables.get("capability_packs"))
        operator_contracts = detach(context_variables.get("operator_contracts")) or []

        # No-op in OSS mode — no capability packs provided by the operator.
        if _is_empty(capability_packs):
            return

        body = _build_managed_context_body(capability_packs, operator_contracts)  # type: ignore[arg-type]
        update_agent_section(agent, _MANAGED_CAP_HEADER, body)

        pack_ids = [
            (p.get("id") or p.get("pack_id") or "?") if isinstance(p, dict) else str(p)
            for p in capability_packs  # type: ignore[union-attr]
        ]
        logger.info(
            "[%s] Injected managed capabilities context (packs: %s)",
            agent_name,
            ", ".join(pack_ids),
        )

    except Exception as exc:
        logger.error(
            "[%s] Failed to inject managed capabilities context: %s",
            agent_name,
            exc,
        )


__all__ = ["inject_managed_capabilities_context"]


