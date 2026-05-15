"""Inject AppGenerator shell preset guidance into planning/schema prompts."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from factory_app.workflows.AppGenerator.tools._hook_utils import update_agent_section

logger = logging.getLogger(__name__)

_SHELL_PRESETS_PATH = Path(__file__).parent / "shell_presets.yaml"
_HEADER = "[SHELL PRESET CONTEXT]"
_EXPECTED_VERSION = 1
_TARGET_AGENTS = {"AppPlanAgent", "AppSchemaAgent"}


@lru_cache(maxsize=1)
def _load_shell_presets() -> Optional[Dict[str, Any]]:
    try:
        with open(_SHELL_PRESETS_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            logger.warning("shell_presets.yaml did not parse as a dict")
            return None
        if data.get("version") != _EXPECTED_VERSION:
            logger.warning(
                "shell_presets.yaml version %s != expected %s",
                data.get("version"),
                _EXPECTED_VERSION,
            )
        return data
    except Exception as exc:
        logger.warning("shell_presets.yaml could not be loaded: %s", exc)
        return None


def _first_sentence(value: Any) -> str:
    text = str(value or "").strip()
    return text.split("\n", 1)[0].strip()


def _format_preset(preset_id: str, preset: Dict[str, Any]) -> str:
    lines = [f"{preset_id}:"]
    description = _first_sentence(preset.get("description"))
    if description:
        lines.append(f"  description: {description}")

    select_when = [
        str(item).strip()
        for item in (preset.get("select_when") or [])
        if str(item).strip()
    ]
    if select_when:
        lines.append("  select_when:")
        lines.extend(f"    - {item}" for item in select_when[:3])

    chrome_default = str(preset.get("chrome_default") or "").strip()
    if chrome_default:
        lines.append(f"  chrome_default: {chrome_default}")

    shell_policy = preset.get("shell_policy")
    if isinstance(shell_policy, dict):
        desktop = shell_policy.get("desktop") if isinstance(shell_policy.get("desktop"), dict) else {}
        mobile = shell_policy.get("mobile") if isinstance(shell_policy.get("mobile"), dict) else {}
        if desktop:
            lines.append(
                "  desktop: "
                f"global={desktop.get('global')}, local={desktop.get('local')}, footer={desktop.get('footer')}"
            )
        if mobile:
            lines.append(
                "  mobile: "
                f"global={mobile.get('global')}, local={mobile.get('local')}, footer={mobile.get('footer')}"
            )
        if shell_policy.get("maxMobileItems") is not None:
            lines.append(f"  maxMobileItems: {shell_policy.get('maxMobileItems')}")

    page_guidance = preset.get("page_guidance")
    if isinstance(page_guidance, dict):
        primary_modes = page_guidance.get("primary_modes")
        if isinstance(primary_modes, dict) and primary_modes:
            compact = ", ".join(f"{key}={value}" for key, value in primary_modes.items())
            lines.append(f"  page_modes: {compact}")
        navigation_scopes = page_guidance.get("navigation_scopes")
        if isinstance(navigation_scopes, dict) and navigation_scopes:
            compact = ", ".join(f"{key}={value}" for key, value in navigation_scopes.items())
            lines.append(f"  nav_scopes: {compact}")

    return "\n".join(lines)


def _build_shell_preset_body(presets_config: Dict[str, Any]) -> str:
    presets = presets_config.get("presets")
    if not isinstance(presets, dict) or not presets:
        return (
            "Shell preset catalog is empty. Do not emit shell_preset_hint. "
            "Use normal page navigation and shell_config rules only."
        )

    parts = [
        "Shell presets are prompt-time guidance only, not runtime artifacts.",
        "AppPlanAgent may set AppBuildPlan.shell_preset_hint to one preset id or null.",
        "AppSchemaAgent compiles that hint into normal AppPageSchema.navigation, AppPageSchema.shell_mode, and optional shell_config.",
        "Do not emit preset ids into generated app files. Do not create shell actions unless product intent explicitly requires them.",
        "When shell actions need context-aware behavior, use semantic variants[].when fields rather than path or query override rules.",
        "Keep shell_config null when platform defaults are sufficient.",
        "",
        "Available presets:",
    ]

    for preset_id, preset in presets.items():
        if isinstance(preset, dict):
            parts.append(_format_preset(str(preset_id), preset))

    rules = [
        str(item).strip()
        for item in (presets_config.get("rules") or [])
        if str(item).strip()
    ]
    if rules:
        parts.append("")
        parts.append("Rules:")
        parts.extend(f"- {rule}" for rule in rules)

    return "\n\n".join(parts)


def inject_shell_preset_context(agent: Any, messages: List[Dict[str, Any]]) -> None:
    """Inject shell preset context into AppGenerator planning/schema agents."""
    del messages

    agent_name = getattr(agent, "name", "")
    if agent_name not in _TARGET_AGENTS:
        return

    presets_config = _load_shell_presets()
    if not presets_config:
        update_agent_section(
            agent,
            _HEADER,
            "WARNING: Shell preset catalog could not be loaded. "
            "Do not emit shell_preset_hint; use explicit page navigation, shell_config rules, "
            "and semantic shell action variants only.",
        )
        return

    update_agent_section(agent, _HEADER, _build_shell_preset_body(presets_config))
    logger.info("[%s] Injected shell preset context", agent_name)


__all__ = ["inject_shell_preset_context"]
