"""
Hook: Inject Capability Routing Context

Fires as an prompt middleware function on AppPlanAgent.

Reads the AppGenerator capability routing catalog and injects a
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
from functools import lru_cache
from typing import Any

import yaml

from factory_app.workflows._shared.hook_utils import update_agent_section, workflow_context_path

logger = logging.getLogger(__name__)

_ROUTING_PATH = workflow_context_path("AppGenerator", "capability_routing.yaml")
_ROUTING_HEADER = "[CAPABILITY ROUTING CONTEXT]"
_EXPECTED_VERSION = 1


@lru_cache(maxsize=1)
def _load_routing() -> dict[str, Any] | None:
    """Load and cache capability_routing.yaml.  Cache is process-scoped (fine for production).
    Call ``_load_routing.cache_clear()`` in tests that need a fresh load."""
    try:
        with open(_ROUTING_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            logger.warning("capability_routing.yaml did not parse as a dict — ignoring")
            return None
        version = data.get("version")
        if version != _EXPECTED_VERSION:
            logger.warning(
                "capability_routing.yaml version %s != expected %s — "
                "injected context may be incomplete",
                version,
                _EXPECTED_VERSION,
            )
        return data
    except Exception as exc:
        logger.warning("capability_routing.yaml could not be loaded: %s", exc)
        return None



def _format_layer(layer_key: str, layer_data: dict[str, Any]) -> str:
    description = str(layer_data.get("description") or "").strip()
    rule = str(layer_data.get("rule") or "").strip()
    lines = [f"Layer: {layer_key}"]
    if description:
        # First sentence only to keep the block compact
        lines.append(f"  {description.split(chr(10))[0].strip()}")
    if rule:
        lines.append(f"  Rule: {rule.split(chr(10))[0].strip()}")
    return "\n".join(lines)


def _format_packs(packs: list[dict[str, Any]]) -> str:
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
        use_when = str(pack.get("use_when") or "").strip().split("\n")[0].strip()
        avoid_when = str(pack.get("avoid_when") or "").strip().split("\n")[0].strip()
        line = f"  - {pack_id} [{kind}]: {covers}"
        if manifest:
            line += f" (manifest: {manifest})"
        if use_when:
            line += f"\n      use_when: {use_when}"
        if avoid_when:
            line += f"\n      avoid_when: {avoid_when}"
        lines.append(line)
    return "\n".join(lines)


def _build_routing_body(routing: dict[str, Any]) -> str:
    layers: dict[str, Any] = routing.get("layers") or {}
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

    # Surface the naming note in full — it's a critical disambiguation block
    naming_note = str(cap_layer.get("naming_note") or "").strip()
    if naming_note:
        parts.append(f"Naming note:\n{naming_note}")

    # Surface operator-pack guidance when present.
    operator_pack_note = str(cap_layer.get("operator_pack_note") or "").strip()
    if operator_pack_note:
        parts.append(f"Operator capability packs:\n{operator_pack_note}")

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
    messages: list[dict[str, Any]],
) -> None:
    """
    prompt middleware function for AppPlanAgent.

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
        update_agent_section(agent, _ROUTING_HEADER, body)

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




