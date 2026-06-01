"""
Hook: Inject Hosted Capabilities Context

Fires as an update_agent_state hook on AppPlanAgent.

When runtime_capabilities, available_hosted_packs, or pack_sources are
present in context_variables (populated by the hosted mozaiks-app overlay),
this hook injects a compact [HOSTED CAPABILITIES CONTEXT] block into the
AppPlanAgent system message.

Capability source taxonomy injected into the block:
  host_universal  — built-in platform features; never generate them
  framework_pack  — reusable OSS capability pack; AppGenerator generates internals
  hosted_pack     — proprietary hosted capability; use as-is, do not regenerate
  generated_module — AppGenerator should generate module contracts and backend
  external_adapter — generate adapter/client wiring only; backend is third-party

In OSS mode (all three variables are null/empty) this hook is a complete no-op.
The block is never injected and AppGenerator behaviour is unchanged.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from factory_app.workflows.AppGenerator.tools._hook_utils import update_agent_section

logger = logging.getLogger(__name__)

_HOSTED_CAP_HEADER = "[HOSTED CAPABILITIES CONTEXT]"

# ---------------------------------------------------------------------------
# Capability source taxonomy and guidance
# ---------------------------------------------------------------------------

_CAPABILITY_SOURCE_GUIDANCE = """\
Capability source taxonomy:
  host_universal  — Built-in platform feature already provided by the runtime host.
                    Do NOT scaffold or generate code for these. Reference them in
                    service_scope or external_integrations, never as capability packs.
  framework_pack  — Reusable OSS capability pack selected by the app. Generate ONLY
                    app-specific wiring (pack_overlay task): event-flow bindings,
                    facade module actions, page composition. Never regenerate pack internals.
  hosted_pack     — Proprietary hosted capability available in this deployment.
                    Do NOT regenerate its internals. Include it as a capability pack
                    entry with implementation_mode: external_integration and
                    surface_kind: external_integration or module (as declared).
                    The pack is already deployed; only wire it into the generated app.
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


def _format_runtime_capabilities(capabilities: List[Any]) -> str:
    lines = ["Runtime capabilities available in this session:"]
    for cap in capabilities:
        lines.append(f"  - {cap}")
    return "\n".join(lines)


def _format_hosted_packs(packs: List[Any]) -> str:
    lines = ["Hosted capability packs available (do NOT regenerate internals):"]
    for pack in packs:
        if isinstance(pack, dict):
            pack_id = pack.get("id") or pack.get("pack_id") or str(pack)
            label = pack.get("display_name") or pack.get("label") or pack_id
            description = pack.get("description") or ""
            caps = pack.get("capabilities") or []
            supersedes = pack.get("supersedes") or []
            line = f"  - {pack_id} ({label}) [capability_source: hosted_pack]"
            if supersedes:
                line += f" [supersedes: {', '.join(str(s) for s in supersedes)}]"
            if description:
                line += f": {description.strip().split(chr(10))[0]}"
            if caps:
                cap_ids = [
                    (c.get("capability_id") or c) if isinstance(c, dict) else str(c)
                    for c in caps[:4]
                ]
                line += f" | capabilities: {', '.join(cap_ids)}"
            lines.append(line)
        else:
            lines.append(f"  - {pack}")
    return "\n".join(lines)


def _format_pack_surfaces(packs: List[Any]) -> str | None:
    """
    Render surface groupings from pack descriptors.

    Returns a formatted string when at least one pack defines surfaces,
    or None when no surfaces are declared (omits the section entirely).
    """
    surface_lines: list[str] = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        surfaces = pack.get("surfaces") or []
        if not surfaces:
            continue
        pack_id = pack.get("id") or pack.get("pack_id") or "?"
        label = pack.get("display_name") or pack.get("label") or pack_id
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
    if not surface_lines:
        return None
    return "Pack surfaces:\n" + "\n".join(surface_lines)


def _format_pack_supported_domains(packs: List[Any]) -> str | None:
    """
    Render domain fit hints from pack descriptors.

    Returns formatted domain guidance, or None when no pack declares domain hints.
    """
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


def _format_pack_branding(packs: List[Any]) -> str | None:
    """
    Render branding hints from pack descriptors.

    Returns branding guidance, or None when no pack declares branding.
    """
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


def _format_host_generation_rules(packs: List[Any]) -> str | None:
    """
    Render optional generation_rules supplied by the host in pack descriptors.

    Hosts may include a ``generation_rules`` list in any pack descriptor to
    inject host-specific "do not build because the host provides it" guidance.
    Rules are only rendered when at least one pack supplies them; otherwise
    this function returns None and nothing is added to the context block.

    Example host-supplied pack descriptor (e.g. in a hosted product overlay):
        available_hosted_packs:
          - id: some_hosted_pack
            label: Some Hosted Pack
            capability_source: hosted_pack
            generation_rules:
              - Do not generate token tracking modules.
              - Do not generate payment rails.
              - Use hosted_pack dependency/adapters when needed.
    """
    rule_blocks: list[str] = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        rules = pack.get("generation_rules") or []
        if not rules:
            continue
        pack_id = pack.get("id") or pack.get("pack_id") or "?"
        label = pack.get("display_name") or pack.get("label") or pack_id
        rule_lines = "\n".join(f"  - {r}" for r in rules)
        rule_blocks.append(f"{pack_id} ({label}):\n{rule_lines}")
    if not rule_blocks:
        return None
    header = "Host-provided generation rules (do not build what the host already supplies):"
    return header + "\n" + "\n".join(rule_blocks)


def _format_pack_sources(sources: List[Any]) -> str:
    lines = ["Pack source roots (build-time planning reference only):"]
    for src in sources:
        if isinstance(src, dict):
            src_id = src.get("id") or "?"
            kind = src.get("kind") or "?"
            cap_source = src.get("capability_source") or "?"
            lines.append(f"  - {src_id}: kind={kind}, capability_source={cap_source}")
        else:
            lines.append(f"  - {src}")
    lines.append("  Note: pack_sources is planning context only. Do not expand into build_tasks or runtime paths.")
    return "\n".join(lines)


def _build_hosted_context_body(
    runtime_capabilities: List[Any] | None,
    available_hosted_packs: List[Any] | None,
    pack_sources: List[Any] | None,
) -> str:
    parts: list[str] = [_CAPABILITY_SOURCE_GUIDANCE]

    if not _is_empty(runtime_capabilities):
        parts.append(_format_runtime_capabilities(runtime_capabilities))

    if not _is_empty(available_hosted_packs):
        parts.append(_format_hosted_packs(available_hosted_packs))
        parts.append(
            "Planning rules for hosted_pack entries:\n"
            "1. Include each hosted_pack in capability_packs exactly as shown:\n"
            "   {\"capability_pack_id\": \"{pack_id}\", \"capability_source\": \"hosted_pack\",\n"
            "    \"implementation_mode\": \"external_integration\",\n"
            "    \"surface_kind\": \"external_integration\"}\n"
            "   The capability_source field MUST be \"hosted_pack\" — not \"generated_module\".\n"
            "2. Do NOT generate a module_contract build task for the hosted_pack itself —\n"
            "   the module is already deployed in the host.\n"
            "3. For each hosted_pack with surface_kind: external_integration, generate three\n"
            "   build tasks in the app:\n"
            "   a) task_type: api_surface — surface_kind: external_integration\n"
            "      capability_pack_id: {pack_id}  (REQUIRED — do NOT set to null)\n"
            "      initial_agent: ControllerAgent\n"
            "      owned_paths: [\"services/integrations/{pack_id}_client.py\"]\n"
            "      This thin adapter wraps calls to the hosted pack API.\n"
            "      The capability_pack_id identifies which hosted pack template to copy.\n"
            "   b) task_type: module_contract — surface_kind: module  (NOT external_integration)\n"
            "      initial_agent: ConfigMiddlewareAgent\n"
            "      capability_pack_id: {app_owned_facade_id}  (e.g. {pack_id}_dashboard)\n"
            "      This is an app-owned facade module that uses the adapter above.\n"
            "      depends_on: [the api_surface task id above]\n"
            "   c) task_type: page_bundle — surface_kind: ui_only\n"
            "      initial_agent: AppSchemaAgent\n"
            "      owned_paths: UI page yaml files for this feature\n"
            "      depends_on: [the module_contract task id above]\n"
            "4. Page bundles must bind to the app-owned facade module route\n"
            "   (e.g. /api/modules/{facade_id}/), never to /api/modules/{pack_id}/ directly.\n"
            "5. If a hosted_pack supersedes another pack, do NOT include the superseded pack\n"
            "   in capability_packs. Only include the superseding hosted_pack."
        )
        surfaces_block = _format_pack_surfaces(available_hosted_packs)
        if surfaces_block is not None:
            parts.append(surfaces_block)

        domains_block = _format_pack_supported_domains(available_hosted_packs)
        if domains_block is not None:
            parts.append(domains_block)

        branding_block = _format_pack_branding(available_hosted_packs)
        if branding_block is not None:
            parts.append(branding_block)

        generation_rules_block = _format_host_generation_rules(available_hosted_packs)
        if generation_rules_block is not None:
            parts.append(generation_rules_block)

    if not _is_empty(pack_sources):
        parts.append(_format_pack_sources(pack_sources))

    return "\n\n".join(parts)




def inject_hosted_capabilities_context(
    agent: Any,
    messages: List[Dict[str, Any]],
) -> None:
    """
    update_agent_state hook for AppPlanAgent.

    Reads runtime_capabilities, available_hosted_packs, and pack_sources from
    context_variables. No-ops when all three are null/empty (OSS mode).
    Injects [HOSTED CAPABILITIES CONTEXT] when any value is present.
    """
    agent_name = getattr(agent, "name", "")
    if agent_name != "AppPlanAgent":
        return

    try:
        context_variables: Dict[str, Any] = getattr(agent, "context_variables", {}) or {}

        runtime_capabilities = context_variables.get("runtime_capabilities")
        available_hosted_packs = context_variables.get("available_hosted_packs")
        pack_sources = context_variables.get("pack_sources")

        # No-op in OSS mode — all three are null or empty.
        if (
            _is_empty(runtime_capabilities)
            and _is_empty(available_hosted_packs)
            and _is_empty(pack_sources)
        ):
            return

        body = _build_hosted_context_body(
            runtime_capabilities=runtime_capabilities,
            available_hosted_packs=available_hosted_packs,
            pack_sources=pack_sources,
        )
        update_agent_section(agent, _HOSTED_CAP_HEADER, body)

        hosted_pack_ids = []
        if not _is_empty(available_hosted_packs):
            for p in available_hosted_packs:
                if isinstance(p, dict):
                    hosted_pack_ids.append(p.get("id") or p.get("pack_id") or "?")
                else:
                    hosted_pack_ids.append(str(p))

        logger.info(
            "[%s] Injected hosted capabilities context (hosted_packs: %s)",
            agent_name,
            ", ".join(hosted_pack_ids) if hosted_pack_ids else "none",
        )

    except Exception as exc:
        logger.error(
            "[%s] Failed to inject hosted capabilities context: %s",
            agent_name,
            exc,
        )


__all__ = ["inject_hosted_capabilities_context"]

