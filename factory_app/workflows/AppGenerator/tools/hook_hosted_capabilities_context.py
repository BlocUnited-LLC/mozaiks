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

logger = logging.getLogger(__name__)

_HOSTED_CAP_HEADER = "[HOSTED CAPABILITIES CONTEXT]"

# ---------------------------------------------------------------------------
# Capability source taxonomy and guidance
# ---------------------------------------------------------------------------

_CAPABILITY_SOURCE_GUIDANCE = """\
Capability source taxonomy:
  host_universal  — Built-in platform feature already provided by the runtime host.
                    Do NOT scaffold or generate code for these. Reference them in
                    backend_scope or external_integrations, never as capability packs.
  framework_pack  — Reusable OSS AppGenerator capability pack. AppGenerator generates
                    module contracts and backend files from this pack.
  hosted_pack     — Proprietary hosted capability available in this deployment.
                    Do NOT regenerate its internals. Include it as a capability pack
                    entry with implementation_mode: external_integration and
                    surface_kind: external_integration or module (as declared).
                    The pack is already deployed; only wire it into the generated app.
  generated_module — AppGenerator must generate full module contracts and backend files.
  external_adapter — Generate adapter / client wiring only. The actual backend is
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
            "3. For each hosted_pack with surface_kind: external_integration, generate two\n"
            "   build tasks in the app:\n"
            "   a) task_type: api_surface — surface_kind: external_integration\n"
            "      initial_agent: ControllerAgent\n"
            "      owned_paths: [\"backend/integrations/{pack_id}_client.py\"]\n"
            "      This thin adapter wraps calls to the hosted pack API.\n"
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
        generation_rules_block = _format_host_generation_rules(available_hosted_packs)
        if generation_rules_block is not None:
            parts.append(generation_rules_block)

    if not _is_empty(pack_sources):
        parts.append(_format_pack_sources(pack_sources))

    return "\n\n".join(parts)


def _update_section(agent: Any, header: str, body: str) -> None:
    """Append or replace a named section in the agent system message."""
    try:
        current: str = (
            getattr(agent, "system_message", None)
            or getattr(agent, "_system_message", "")
            or ""
        )
        section = f"{header}\n{body}"

        if header in current:
            pre, _, rest = current.partition(header)
            next_section_idx = rest.find("\n\n[")
            after = rest[next_section_idx:] if next_section_idx > 0 else ""
            new_message = f"{pre.rstrip()}\n\n{section}{after}"
        else:
            new_message = f"{current}\n\n{section}" if current else section

        if new_message == current:
            return

        updater = getattr(agent, "update_system_message", None)
        if callable(updater):
            updater(new_message)
        elif hasattr(agent, "_system_message"):
            agent._system_message = new_message
        else:
            setattr(agent, "_system_message", new_message)

    except Exception as exc:
        logger.error(
            "[%s] Failed to update system message section %s: %s",
            getattr(agent, "name", "?"),
            header,
            exc,
        )


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
        _update_section(agent, _HOSTED_CAP_HEADER, body)

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
