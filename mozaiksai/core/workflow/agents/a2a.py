from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class A2AAgentSpec:
    """Declarative spec for a remote A2A-backed agent."""

    name: str
    url: str
    max_reconnects: int = 3
    polling_interval: float = 0.5
    silent: bool | None = None
    client: dict[str, Any] = field(default_factory=dict)


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sanitize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text)
    return out


def _build_client_config_kwargs(client_cfg: Mapping[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}

    # Keep this list narrow and explicit so declarative config stays predictable.
    if "streaming" in client_cfg:
        kwargs["streaming"] = _as_bool(client_cfg.get("streaming"), default=True)
    if "polling" in client_cfg:
        kwargs["polling"] = _as_bool(client_cfg.get("polling"), default=False)
    if "use_client_preference" in client_cfg:
        kwargs["use_client_preference"] = _as_bool(client_cfg.get("use_client_preference"), default=False)

    if "accepted_output_modes" in client_cfg:
        kwargs["accepted_output_modes"] = _sanitize_string_list(client_cfg.get("accepted_output_modes"))
    if "extensions" in client_cfg:
        kwargs["extensions"] = _sanitize_string_list(client_cfg.get("extensions"))
    if "supported_transports" in client_cfg:
        kwargs["supported_transports"] = _sanitize_string_list(client_cfg.get("supported_transports"))

    # Pass through raw push notification configs if they are already list-shaped.
    push_cfg = client_cfg.get("push_notification_configs")
    if isinstance(push_cfg, list):
        kwargs["push_notification_configs"] = push_cfg

    return kwargs


def load_a2a_agent_specs(workflow_config: Mapping[str, Any] | None) -> dict[str, A2AAgentSpec]:
    """Return mapping of agent name -> A2AAgentSpec for declared remote agents."""

    if not isinstance(workflow_config, Mapping):
        return {}

    raw_section = workflow_config.get("a2a")
    if not isinstance(raw_section, Mapping):
        return {}

    entries = raw_section.get("agents")
    if not isinstance(entries, list):
        return {}

    specs: dict[str, A2AAgentSpec] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue

        enabled = _as_bool(entry.get("enabled"), default=True)
        if not enabled:
            continue

        name = entry.get("name")
        url = entry.get("url")
        if not isinstance(name, str) or not name.strip():
            logger.warning("[A2A] Skipping A2A agent entry without valid 'name'")
            continue
        if not isinstance(url, str) or not url.strip():
            logger.warning("[A2A] Skipping A2A agent '%s' without valid 'url'", name)
            continue

        client_cfg = entry.get("client")
        client_cfg_dict = dict(client_cfg) if isinstance(client_cfg, Mapping) else {}

        specs[name.strip()] = A2AAgentSpec(
            name=name.strip(),
            url=url.strip(),
            max_reconnects=_as_int(entry.get("max_reconnects"), default=3),
            polling_interval=_as_float(entry.get("polling_interval"), default=0.5),
            silent=entry.get("silent") if isinstance(entry.get("silent"), bool) else None,
            client=client_cfg_dict,
        )

    return specs


def create_a2a_remote_agent(spec: A2AAgentSpec, *, context_variables: Any = None) -> Any:
    """Instantiate an AG2 A2A remote agent from declarative spec."""

    try:
        from autogen.a2a import A2aRemoteAgent
        from autogen.a2a.client import ClientConfig
    except Exception as err:  # pragma: no cover - depends on optional extras
        raise RuntimeError(
            "A2A support is unavailable. Install AG2 with A2A extras (e.g. `ag2[a2a,openai,lmm]`)."
        ) from err

    client_kwargs = _build_client_config_kwargs(spec.client)
    client_config = ClientConfig(**client_kwargs)

    agent = A2aRemoteAgent(
        url=spec.url,
        name=spec.name,
        silent=spec.silent,
        client_config=client_config,
        max_reconnects=spec.max_reconnects,
        polling_interval=spec.polling_interval,
    )

    # Share the runtime context object so A2A and local agents observe the same state.
    if context_variables is not None:
        agent.context_variables = context_variables

    agent._mozaiks_a2a_url = spec.url  # type: ignore[attr-defined]
    return agent


__all__ = [
    "A2AAgentSpec",
    "load_a2a_agent_specs",
    "create_a2a_remote_agent",
]

