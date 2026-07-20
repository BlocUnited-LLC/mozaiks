"""
Hook: Inject Managed Capabilities Context

Fires as an prompt middleware function on AppPlanAgent.

When ``capability_packs`` is present and non-empty in context_variables
(populated by an operator at launch), this hook injects a compact
[MANAGED CAPABILITIES CONTEXT] block into the AppPlanAgent system message.

Capability source taxonomy injected into the block:
  host_universal  — built-in platform features; never generate them
  managed_capability     — operator-managed capability; use as-is, do not regenerate
  generated_module — AppGenerator should generate full module contracts
  external_adapter — generate adapter/client wiring only; backend is third-party

In OSS mode (capability_packs is null or empty) this hook is a complete no-op.
The block is never injected and factory workflow behaviour is unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section

logger = logging.getLogger(__name__)

_MANAGED_CAP_HEADER = "[MANAGED CAPABILITIES CONTEXT]"

# ---------------------------------------------------------------------------
# Capability source taxonomy guidance (always included when packs are present)
# ---------------------------------------------------------------------------

_CAPABILITY_SOURCE_GUIDANCE = """\
Capability source taxonomy:
  host_universal  — Built-in platform feature already provided by the runtime host.
                    Do NOT scaffold or generate code for these. Reference them in
                    service_scope or external_integrations, never as capability packs.
  managed_capability     — Operator-managed capability available in this deployment.
                    Do NOT regenerate its internals. Include it as a capability pack
                    entry with implementation_mode: external_integration and
                    surface_kind: external_integration or module (as declared).
                    The pack is already deployed; only wire it into the generated app.
                    Never create modules/{capability_pack_id}/ for this source.
                    For user-facing pages/actions, create an app-owned facade module
                    with a different id, then bind pages to that facade.
  framework_pack  — OSS or operator framework pack. Generate only the declared
                    app-specific wiring; do not invent undeclared pack internals.
  generated_module — AppGenerator must generate full module contracts and module backend files.
  external_adapter — Generate adapter / client wiring only. The actual service is
                    third-party or separately deployed."""


def _is_empty(value: Any) -> bool:
    """Return True if value is None, empty list, or empty dict."""
    if value is None:
        return True
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _format_managed_capabilities(packs: list[Any]) -> str:
    lines = [
        "Managed capabilities available (do NOT regenerate internals):",
        "For each listed capability, output capability_packs[].capability_pack_id exactly as the listed id.",
        "Do not rename ids, add managed_ prefixes, or plan modules/{capability_pack_id}/ outputs.",
        "If the managed capability backs pages or user actions, plan a separate app-owned facade module plus data_models, business_services, and page_bundle tasks.",
    ]
    for pack in packs:
        if isinstance(pack, dict):
            pack_id = pack.get("id") or pack.get("pack_id") or str(pack)
            label = pack.get("display_name") or pack.get("label") or pack_id
            description = pack.get("description") or ""
            caps = pack.get("capabilities") or []
            cap_source = str(pack.get("capability_source") or "managed_capability").strip()
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
       Rendered as explicit facade-module instructions so the LLM knows
       to generate a module_contract task for the facade rather than
       generating the managed capability itself.

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
            surface_lines.append(f"{pack_id} ({label}) facade modules (do NOT regenerate {pack_id}/):")
            for facade in facades:
                if not isinstance(facade, dict):
                    continue
                module_id = facade.get("module_id") or "?"
                provider = facade.get("provider_module") or pack_id
                pages = facade.get("pages") or []
                actions = facade.get("provider_actions") or []
                line = (
                    f"  - Generate facade module '{module_id}' that wraps {provider}."
                    f" Plan a module_contract task for '{module_id}', NOT for '{pack_id}'."
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
        intents = when.get("intent_any") if isinstance(when, dict) else []
        intent_text = ", ".join(str(intent) for intent in intents) if isinstance(intents, list) else ""
        suffix = f" -> {action}" if action else ""
        lines.append(f"  - {rule_id}{suffix}" + (f" when intent_any: {intent_text}" if intent_text else ""))
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
        required = _format_contract_list_items(contract.get("required_outputs"), key="path")
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
    parts: list[str] = [_CAPABILITY_SOURCE_GUIDANCE]

    parts.append(_format_managed_capabilities(capability_packs))
    parts.append(
        "Planning rules for managed_capability entries:\n"
        "1. Include each managed_capability in AppBuildPlan.capability_packs exactly once with\n"
        "   these REQUIRED fields and values:\n"
        "   - capability_pack_id: the managed capability id exactly as listed above\n"
        "   - capability_source: managed_capability\n"
        "   - implementation_mode: external_integration\n"
        "   - surface_kind: external_integration\n"
        "   The capability_source field MUST be present and MUST be \"managed_capability\".\n"
        "   Do not substitute pack_type: managed_capability for capability_source.\n"
        "   Do not mark the managed capability entry as generated_module.\n"
        "2. Do NOT generate a module_contract build task for the managed_capability itself —\n"
        "   the module is already deployed in the host.\n"
        "3. For each managed_capability with surface_kind: external_integration, generate one\n"
        "   api_surface adapter task for the managed capability. Then, for each selected active\n"
        "   pack surface that needs app-owned actions or page endpoints, generate the\n"
        "   corresponding app-owned facade module and page tasks declared by the typed\n"
        "   operator contract:\n"
        "   a) task_type: api_surface — surface_kind: external_integration\n"
        "      capability_pack_id: {pack_id}  (REQUIRED — do NOT set to null)\n"
        "      initial_agent: ControllerAgent\n"
        "      owned_paths: [\"services/integrations/{pack_id}_client.py\"]\n"
        "      This thin adapter is app-level service code and wraps calls to the managed capability API.\n"
        "      Do not place it under modules/{facade_id}/services/ or modules/{pack_id}/services/.\n"
        "      The capability_pack_id identifies which managed capability template to copy.\n"
        "   b) task_type: module_contract — surface_kind: module  (NOT external_integration)\n"
        "      initial_agent: ConfigMiddlewareAgent\n"
        "      capability_pack_id: {app_owned_facade_id}  (e.g. {pack_id}_dashboard)\n"
        "      This is an app-owned facade module that uses the adapter above.\n"
        "      depends_on: [the api_surface task id above]\n"
        "      The facade module is generated_module; the managed capability remains managed_capability.\n"
        "      Also plan the matching data_models and business_services tasks for this\n"
        "      facade module unless selected pack templates provide those outputs.\n"
        "   c) task_type: page_bundle — surface_kind: ui_only\n"
        "      initial_agent: AppSchemaAgent\n"
        "      owned_paths: UI page yaml files for this feature\n"
        "      depends_on: [the module_contract task id above]\n"
        "4. Page bundles must bind to the app-owned facade module route\n"
        "   (e.g. /api/modules/{facade_id}/), never to /api/modules/{pack_id}/ directly.\n"
        "5. Before emitting AppBuildPlan, verify every page config_hint.api_endpoint for\n"
        "   managed-capability features uses /api/modules/{app_owned_facade_id}/{action_id}.\n"
        "   Direct /api/modules/{pack_id}/, managed backing module, wallet, managed_billing,\n"
        "   managed_usage, or managed_entitlements endpoints are invalid page bindings.\n"
        "6. If the operator contract declares required_outputs, forbidden_outputs, facades,\n"
        "   or pages, treat those typed values as authoritative. Do not invent alternate\n"
        "   facade ids, adapter paths, or page routes for the selected managed capability."
    )

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
        context_variables: dict[str, Any] = getattr(agent, "context_variables", {}) or {}
        capability_packs = context_variables.get("capability_packs")
        operator_contracts = context_variables.get("operator_contracts") or []

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


