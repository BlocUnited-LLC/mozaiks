# ==============================================================================
# FILE: mozaiksai/core/adapters/registry.py
# DESCRIPTION: Declarative adapter registry — resolves provider adapters named
#              in app/config/adapters.yaml and verifies each one satisfies the
#              port it claims to implement.
#
#              This module is a MECHANISM only. It defines no ports and knows
#              no providers. Both the port and the implementation are named by
#              import reference in the app's own config, so they may live in the
#              app bundle, in a capability pack, or in a hosted product — the
#              runtime never needs to know which.
#
#              That separation is deliberate. A port the OSS runtime does not
#              call through does not belong in OSS; it belongs beside whatever
#              consumes it. This loader is what makes that possible without
#              giving up load-time verification.
# ==============================================================================
"""Declarative, conformance-checked adapter resolution."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = "config/adapters.yaml"
_SCHEMA_VERSION = "mozaiks.adapters.v1"

_AREA_KEYS = {"port", "providers", "active", "active_env"}
_ROOT_KEYS = {"schema_version", "areas"}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AdapterConfigError(ValueError):
    """adapters.yaml is missing required structure or declares unknown keys."""


class AdapterResolutionError(LookupError):
    """A declared import reference could not be resolved."""


class AdapterConformanceError(TypeError):
    """A resolved adapter does not satisfy the port it is declared against."""


class UnknownAdapterError(LookupError):
    """A lookup named an area or provider that is not registered."""


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------


def _resolve_reference(reference: str, *, what: str) -> Any:
    """Import ``module:symbol`` (or a bare module) and return the object.

    Raises:
        AdapterResolutionError: with the failing reference named, so an
            operator can act on the message without reading a traceback.
    """
    ref = str(reference or "").strip()
    if not ref:
        raise AdapterResolutionError(f"{what}: empty import reference")

    module_name, _, symbol = ref.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise AdapterResolutionError(
            f"{what}: cannot import module {module_name!r} from reference {ref!r} -> {exc}"
        ) from exc

    if not symbol:
        return module
    try:
        return getattr(module, symbol)
    except AttributeError as exc:
        raise AdapterResolutionError(
            f"{what}: module {module_name!r} has no attribute {symbol!r}"
        ) from exc


def _port_members(port: Any) -> tuple[str, ...]:
    """Public method names a port requires.

    Read from ``__protocol_attrs__`` when the port is a ``Protocol``; otherwise
    fall back to public callables declared on the class. Attribute presence is
    checked rather than ``issubclass`` because ``runtime_checkable`` protocols
    only support ``issubclass`` for method-only protocols, and the failure mode
    is an opaque TypeError rather than a message naming the missing method.
    """
    declared = getattr(port, "__protocol_attrs__", None)
    if declared:
        return tuple(sorted(str(name) for name in declared))
    return tuple(
        sorted(
            name
            for name, value in vars(port).items()
            if not name.startswith("_") and callable(value)
        )
    )


def _assert_conformance(adapter: Any, port: Any, *, area: str, provider_id: str) -> None:
    required = _port_members(port)
    if not required:
        raise AdapterConformanceError(
            f"adapters.yaml area {area!r}: port {port!r} declares no members to verify; "
            "it cannot be used as a conformance contract"
        )
    missing = [name for name in required if not hasattr(adapter, name)]
    if missing:
        raise AdapterConformanceError(
            f"adapters.yaml area {area!r} provider {provider_id!r}: "
            f"{adapter!r} does not satisfy {getattr(port, '__name__', port)!r} — "
            f"missing {', '.join(missing)}"
        )


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterAreaConfig:
    """One capability area and the providers declared for it."""

    area: str
    port_ref: str
    providers: dict[str, str]
    active: str | None = None
    active_env: str | None = None

    def active_provider_id(self, env: dict[str, str] | None = None) -> str | None:
        """Resolve which provider is active.

        ``active_env`` wins when set and non-empty, so an operator can switch
        providers per deployment without editing the declaration. The
        declaration answers *what exists*; runtime config answers *what is on*.
        """
        source = os.environ if env is None else env
        if self.active_env:
            from_env = str(source.get(self.active_env, "") or "").strip()
            if from_env:
                return from_env
        return self.active


@dataclass(frozen=True)
class AdaptersConfig:
    areas: dict[str, AdapterAreaConfig] = field(default_factory=dict)


def _parse_area(area: str, raw: Any) -> AdapterAreaConfig:
    if not isinstance(raw, dict):
        raise AdapterConfigError(f"adapters.yaml area {area!r} must be a mapping")

    unknown = set(raw) - _AREA_KEYS
    if unknown:
        raise AdapterConfigError(
            f"adapters.yaml area {area!r} declares unknown key(s): {sorted(unknown)}. "
            f"Allowed: {sorted(_AREA_KEYS)}"
        )

    port_ref = str(raw.get("port") or "").strip()
    if not port_ref:
        raise AdapterConfigError(f"adapters.yaml area {area!r} must declare 'port'")

    providers_raw = raw.get("providers")
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise AdapterConfigError(
            f"adapters.yaml area {area!r} must declare a non-empty 'providers' mapping"
        )

    providers: dict[str, str] = {}
    for provider_id, ref in providers_raw.items():
        pid = str(provider_id or "").strip()
        if not pid:
            raise AdapterConfigError(f"adapters.yaml area {area!r} has an empty provider id")
        if not str(ref or "").strip():
            raise AdapterConfigError(
                f"adapters.yaml area {area!r} provider {pid!r} has an empty reference"
            )
        providers[pid] = str(ref).strip()

    active = raw.get("active")
    active = str(active).strip() if active is not None else None
    if active and active not in providers:
        raise AdapterConfigError(
            f"adapters.yaml area {area!r} declares active provider {active!r}, "
            f"which is not in providers: {sorted(providers)}"
        )

    active_env = raw.get("active_env")
    active_env = str(active_env).strip() if active_env is not None else None

    return AdapterAreaConfig(
        area=area,
        port_ref=port_ref,
        providers=providers,
        active=active or None,
        active_env=active_env or None,
    )


def parse_adapters_config(raw: Any) -> AdaptersConfig:
    """Validate a parsed adapters.yaml document."""
    if not isinstance(raw, dict):
        raise AdapterConfigError("adapters.yaml must be a mapping")

    unknown = set(raw) - _ROOT_KEYS
    if unknown:
        raise AdapterConfigError(
            f"adapters.yaml declares unknown top-level key(s): {sorted(unknown)}. "
            f"Allowed: {sorted(_ROOT_KEYS)}"
        )

    version = str(raw.get("schema_version") or "").strip()
    if version != _SCHEMA_VERSION:
        raise AdapterConfigError(
            f"adapters.yaml schema_version must be {_SCHEMA_VERSION!r}, got {version!r}"
        )

    areas_raw = raw.get("areas")
    if not isinstance(areas_raw, dict) or not areas_raw:
        raise AdapterConfigError("adapters.yaml must declare a non-empty 'areas' mapping")

    areas = {
        str(area).strip(): _parse_area(str(area).strip(), body)
        for area, body in areas_raw.items()
    }
    return AdaptersConfig(areas=areas)


def load_adapters_config(app_root: Path) -> AdaptersConfig | None:
    """Load ``app/config/adapters.yaml``.

    Returns:
        Parsed config, or None when the file does not exist — apps that declare
        no adapters are unaffected.

    Raises:
        AdapterConfigError: when the file exists but is invalid.
    """
    path = Path(app_root) / _CONFIG_PATH
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AdapterConfigError(f"adapters.yaml is not valid YAML: {exc}") from exc
    return parse_adapters_config(raw)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisteredAdapter:
    area: str
    provider_id: str
    reference: str
    adapter: Any
    port: Any


class AdapterRegistry:
    """Resolved adapters keyed by ``(area, provider_id)``.

    Construction is the verification step: every declared reference is imported
    and checked against its port before the registry exists. A registry that
    was built successfully contains only conforming adapters.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], RegisteredAdapter] = {}
        self._active: dict[str, str] = {}

    def register(self, entry: RegisteredAdapter, *, active: bool = False) -> None:
        self._entries[(entry.area, entry.provider_id)] = entry
        if active:
            self._active[entry.area] = entry.provider_id

    def get(self, area: str, provider_id: str) -> Any:
        try:
            return self._entries[(area, provider_id)].adapter
        except KeyError:
            known = sorted(pid for a, pid in self._entries if a == area)
            raise UnknownAdapterError(
                f"no adapter registered for area {area!r} provider {provider_id!r}. "
                f"Registered for this area: {known or 'none'}"
            ) from None

    def active(self, area: str) -> Any:
        provider_id = self._active.get(area)
        if provider_id is None:
            raise UnknownAdapterError(
                f"area {area!r} has no active provider. Set 'active' or its "
                f"'active_env' variable in adapters.yaml."
            )
        return self.get(area, provider_id)

    def active_provider_id(self, area: str) -> str | None:
        return self._active.get(area)

    def areas(self) -> tuple[str, ...]:
        return tuple(sorted({area for area, _ in self._entries}))

    def providers(self, area: str) -> tuple[str, ...]:
        return tuple(sorted(pid for a, pid in self._entries if a == area))

    def __len__(self) -> int:
        return len(self._entries)


def build_adapter_registry(
    config: AdaptersConfig,
    *,
    env: dict[str, str] | None = None,
) -> AdapterRegistry:
    """Resolve and verify every adapter declared in *config*.

    Every declared provider is imported and conformance-checked, not just the
    active one — a declaration that cannot be satisfied is a defect regardless
    of which provider happens to be switched on today, and finding it at
    startup is the entire point.

    Raises:
        AdapterResolutionError: a port or provider reference does not resolve.
        AdapterConformanceError: a provider does not satisfy its declared port.
    """
    registry = AdapterRegistry()
    for area, area_config in sorted(config.areas.items()):
        port = _resolve_reference(
            area_config.port_ref, what=f"adapters.yaml area {area!r} port"
        )
        active_id = area_config.active_provider_id(env)
        if active_id and active_id not in area_config.providers:
            raise AdapterConfigError(
                f"adapters.yaml area {area!r}: active provider {active_id!r} is not "
                f"declared. Available: {sorted(area_config.providers)}"
            )
        for provider_id, reference in sorted(area_config.providers.items()):
            adapter = _resolve_reference(
                reference,
                what=f"adapters.yaml area {area!r} provider {provider_id!r}",
            )
            _assert_conformance(adapter, port, area=area, provider_id=provider_id)
            registry.register(
                RegisteredAdapter(
                    area=area,
                    provider_id=provider_id,
                    reference=reference,
                    adapter=adapter,
                    port=port,
                ),
                active=(provider_id == active_id),
            )
    return registry


def load_adapter_registry(
    app_root: Path,
    *,
    env: dict[str, str] | None = None,
) -> AdapterRegistry | None:
    """Load and verify adapters.yaml for *app_root*, or None when absent."""
    config = load_adapters_config(app_root)
    if config is None:
        return None
    return build_adapter_registry(config, env=env)


__all__ = [
    "AdapterAreaConfig",
    "AdapterConfigError",
    "AdapterConformanceError",
    "AdapterRegistry",
    "AdapterResolutionError",
    "AdaptersConfig",
    "RegisteredAdapter",
    "UnknownAdapterError",
    "build_adapter_registry",
    "load_adapter_registry",
    "load_adapters_config",
    "parse_adapters_config",
]
