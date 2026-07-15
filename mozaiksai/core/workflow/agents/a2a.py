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


def _build_client_config_kwargs(client_cfg: Mapping[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}

    # Keep this list narrow and explicit so declarative config stays predictable.
    if "streaming" in client_cfg:
        kwargs["streaming"] = _as_bool(client_cfg.get("streaming"), default=True)
    if "timeout" in client_cfg:
        kwargs["timeout"] = _as_float(client_cfg.get("timeout"), default=60.0)
    if "input_required_timeout" in client_cfg:
        raw_timeout = client_cfg.get("input_required_timeout")
        kwargs["input_required_timeout"] = None if raw_timeout is None else _as_float(raw_timeout, default=60.0)
    if "history_length" in client_cfg:
        kwargs["history_length"] = _as_int(client_cfg.get("history_length"), default=0) or None
    if "tenant" in client_cfg and isinstance(client_cfg.get("tenant"), str):
        kwargs["tenant"] = str(client_cfg["tenant"]).strip() or None
    if "prefer" in client_cfg and isinstance(client_cfg.get("prefer"), str):
        prefer = str(client_cfg["prefer"]).strip().lower()
        if prefer in {"jsonrpc", "rest", "grpc"}:
            kwargs["prefer"] = prefer

    headers = client_cfg.get("headers")
    if isinstance(headers, Mapping):
        kwargs["headers"] = {str(k): str(v) for k, v in headers.items()}

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
        from ag2 import Agent
        from ag2.a2a import A2AConfig
    except Exception as err:  # pragma: no cover - depends on optional extras
        raise RuntimeError(
            "A2A support is unavailable. Install AG2 with A2A extras (e.g. `ag2[a2a,openai]`)."
        ) from err

    client_kwargs = _build_client_config_kwargs(spec.client)
    a2a_config = A2AConfig(  # type: ignore[abstract]
        card_url=spec.url,
        max_reconnects=spec.max_reconnects,
        polling_interval=spec.polling_interval,
        **client_kwargs,
    )
    agent = Agent(
        name=spec.name,
        prompt=f"Remote A2A agent: {spec.name}",
        config=a2a_config,
    )

    # Share the runtime context object so A2A and local agents observe the same state.
    if context_variables is not None:
        agent.context_variables = context_variables  # type: ignore[attr-defined]

    agent._mozaiks_a2a_url = spec.url  # type: ignore[attr-defined]
    return agent


__all__ = [
    "A2AAgentSpec",
    "load_a2a_agent_specs",
    "create_a2a_remote_agent",
]

