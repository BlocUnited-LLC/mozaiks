"""
Hook: Inject Capability Routing Context

Fires as an update_agent_state hook on AppPlanAgent.

Reads capability_routing.yaml from the same tools/ directory and injects a
compact [CAPABILITY ROUTING CONTEXT] block into the AppPlanAgent system message.

This block tells AppPlanAgent the four routing layers — runtime_provided,
ai_workflow, capability_pack, and custom_owned — and which known capability packs
exist at what capability_kind. It fires on every AppPlanAgent turn so the rules
are always in scope when the plan is being written.

This is unconditional (unlike the hosted capabilities hook which no-ops in OSS
mode). The routing rules are always relevant and do not change per deployment.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROUTING_PATH = Path(__file__).parent / "capability_routing.yaml"
_ROUTING_HEADER = "[CAPABILITY ROUTING CONTEXT]"


def _load_routing() -> Optional[Dict[str, Any]]:
    try:
        import yaml  # type: ignore

        with open(_ROUTING_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except Exception as exc:
        logger.warning("capability_routing.yaml could not be loaded: %s", exc)
        return None


def _update_section(agent: Any, header: str, body: str) -> None:
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


def _format_layer(layer_key: str, layer_data: Dict[str, Any]) -> str:
    description = str(layer_data.get("description") or "").strip()
    rule = str(layer_data.get("rule") or "").strip()
    lines = [f"Layer: {layer_key}"]
    if description:
        # First sentence only to keep the block compact
        lines.append(f"  {description.split(chr(10))[0].strip()}")
    if rule:
        lines.append(f"  Rule: {rule.split(chr(10))[0].strip()}")
    return "\n".join(lines)


def _format_packs(packs: List[Dict[str, Any]]) -> str:
    if not packs:
        return ""
    lines = ["Known capability packs (select; do not regenerate internals):"]
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        pack_id = str(pack.get("id") or "").strip()
        covers = str(pack.get("covers") or "").strip().split("\n")[0].strip()
        kind = str(pack.get("capability_kind") or "").strip()
        manifest = str(pack.get("manifest") or "").strip()
        line = f"  - {pack_id} [{kind}]: {covers}"
        if manifest:
            line += f" (manifest: {manifest})"
        lines.append(line)
    return "\n".join(lines)


def _build_routing_body(routing: Dict[str, Any]) -> str:
    layers: Dict[str, Any] = routing.get("layers") or {}
    parts: list[str] = []

    # Always show all four layers in a compact form
    for layer_key in ("runtime_provided", "ai_workflow", "capability_pack", "custom_owned"):
        layer_data = layers.get(layer_key)
        if isinstance(layer_data, dict):
            parts.append(_format_layer(layer_key, layer_data))

    # Surface the known capability packs explicitly
    cap_layer = layers.get("capability_pack") or {}
    packs = [p for p in (cap_layer.get("packs") or []) if isinstance(p, dict)]
    packs_text = _format_packs(packs)
    if packs_text:
        parts.append(packs_text)

    # Surface the naming note if present
    naming_note = str(cap_layer.get("naming_note") or "").strip()
    if naming_note:
        parts.append(f"Naming note: {naming_note.split(chr(10))[0].strip()}")

    parts.append(
        "Decision order:\n"
        "  1. runtime_provided? → reference only, never generate\n"
        "  2. needs AI reasoning/orchestration? → workflow_touchpoint + data module\n"
        "  3. matches a known capability_pack? → select it; generate only app-specific wiring\n"
        "  4. custom/bring-your-own? → module.yaml action interfaces + empty stubs only"
    )

    return "\n\n".join(parts)


def inject_capability_routing_context(
    agent: Any,
    messages: List[Dict[str, Any]],
) -> None:
    """
    update_agent_state hook for AppPlanAgent.

    Reads capability_routing.yaml and injects [CAPABILITY ROUTING CONTEXT] into
    the agent system message. Always fires — routing rules are deployment-independent.
    """
    agent_name = getattr(agent, "name", "")
    if agent_name != "AppPlanAgent":
        return

    try:
        routing = _load_routing()
        if not routing:
            return

        body = _build_routing_body(routing)
        _update_section(agent, _ROUTING_HEADER, body)

        layers = routing.get("layers") or {}
        cap_packs = [
            str(p.get("id") or "")
            for p in ((layers.get("capability_pack") or {}).get("packs") or [])
            if isinstance(p, dict)
        ]
        logger.info(
            "[%s] Injected capability routing context (known packs: %s)",
            agent_name,
            ", ".join(cap_packs) if cap_packs else "none",
        )

    except Exception as exc:
        logger.error(
            "[%s] Failed to inject capability routing context: %s",
            agent_name,
            exc,
        )


__all__ = ["inject_capability_routing_context"]
