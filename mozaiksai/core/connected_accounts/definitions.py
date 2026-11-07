"""Account connections that installed plugins declare.

A plugin module that needs a user's third-party account declares it in
``modules/<module_id>/contracts/account_connections.yaml``::

    schema_version: mozaiks.account_connections.v1
    connections:
      - id: google_calendar
        provider: google
        label: Google Calendar
        description: See and add events on your own Google Calendar.
        scopes:
          - https://www.googleapis.com/auth/calendar.events

The declaration is the plugin's request, not a grant: each user decides
whether to connect, and the app's own admins decide which declared
connections the app offers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .providers import known_providers

SCHEMA_VERSION = "mozaiks.account_connections.v1"
CONTRACT_RELATIVE_PATH = "contracts/account_connections.yaml"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_SCOPE_RE = re.compile(r"^[A-Za-z0-9._:/+\-]+$")


class AccountConnectionsConfigError(ValueError):
    """A plugin's account connection declaration is invalid."""


@dataclass(frozen=True)
class AccountConnection:
    id: str
    module_id: str
    provider: str
    label: str
    description: str
    scopes: tuple[str, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module_id": self.module_id,
            "provider": self.provider,
            "label": self.label,
            "description": self.description,
            "scopes": list(self.scopes),
        }


def _text(raw: Any, field: str, where: str, *, limit: int) -> str:
    value = str(raw or "").strip()
    if not value or len(value) > limit:
        raise AccountConnectionsConfigError(f"{where}: {field} is required (at most {limit} characters)")
    return value


def _parse(module_id: str, data: Any, where: str) -> list[AccountConnection]:
    if not isinstance(data, dict):
        raise AccountConnectionsConfigError(f"{where}: must be a mapping")
    unknown = sorted(set(data) - {"schema_version", "connections"})
    if unknown:
        raise AccountConnectionsConfigError(f"{where}: unsupported fields {unknown}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise AccountConnectionsConfigError(f"{where}: schema_version must be {SCHEMA_VERSION!r}")
    rows = data.get("connections")
    if not isinstance(rows, list) or not rows:
        raise AccountConnectionsConfigError(f"{where}: connections must be a non-empty list")
    connections: list[AccountConnection] = []
    for index, row in enumerate(rows):
        row_where = f"{where}: connections[{index}]"
        if not isinstance(row, dict):
            raise AccountConnectionsConfigError(f"{row_where} must be a mapping")
        unknown = sorted(set(row) - {"id", "provider", "label", "description", "scopes"})
        if unknown:
            raise AccountConnectionsConfigError(f"{row_where} has unsupported fields {unknown}")
        connection_id = str(row.get("id") or "").strip()
        if not _ID_RE.fullmatch(connection_id):
            raise AccountConnectionsConfigError(f"{row_where}.id must be a lowercase identifier")
        provider = str(row.get("provider") or "").strip()
        if provider not in known_providers():
            raise AccountConnectionsConfigError(
                f"{row_where}.provider must be one of {sorted(known_providers())}, not {provider!r}"
            )
        scopes = row.get("scopes")
        if not isinstance(scopes, list) or not scopes or not all(
            isinstance(scope, str) and _SCOPE_RE.fullmatch(scope.strip()) for scope in scopes
        ):
            raise AccountConnectionsConfigError(f"{row_where}.scopes must be a non-empty list of scope strings")
        connections.append(AccountConnection(
            id=connection_id,
            module_id=module_id,
            provider=provider,
            label=_text(row.get("label"), "label", row_where, limit=80),
            description=_text(row.get("description"), "description", row_where, limit=300),
            scopes=tuple(dict.fromkeys(scope.strip() for scope in scopes)),
        ))
    return connections


def load_module_account_connections(modules: Iterable[tuple[str, Path]]) -> tuple[AccountConnection, ...]:
    """Every account connection the given ``(module_id, module_dir)`` pairs declare, ordered by id.

    The host passes the modules it actually loaded, so a module that failed to
    load or is not installed offers nothing. Raises
    :class:`AccountConnectionsConfigError` for an invalid declaration or two
    modules declaring the same connection id.
    """
    found: dict[str, AccountConnection] = {}
    for module_id, module_dir in sorted(modules, key=lambda pair: pair[0]):
        contract = Path(module_dir) / CONTRACT_RELATIVE_PATH
        if not contract.is_file():
            continue
        where = f"modules/{module_id}/{CONTRACT_RELATIVE_PATH}"
        try:
            data = yaml.safe_load(contract.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise AccountConnectionsConfigError(f"{where}: invalid YAML ({exc})") from exc
        for connection in _parse(module_id, data, where):
            prior = found.get(connection.id)
            if prior is not None:
                raise AccountConnectionsConfigError(
                    f"{where}: connection id {connection.id!r} is already declared by module {prior.module_id!r}"
                )
            found[connection.id] = connection
    return tuple(found[key] for key in sorted(found))


def load_account_connections(app_root: str | Path) -> tuple[AccountConnection, ...]:
    """Every account connection the modules under ``<app_root>/modules`` declare (for tools and tests)."""
    modules_dir = Path(app_root) / "modules"
    if not modules_dir.is_dir():
        return ()
    return load_module_account_connections(
        (module_dir.name, module_dir)
        for module_dir in modules_dir.iterdir()
        if module_dir.is_dir() and (module_dir / "module.yaml").is_file()
    )


__all__ = [
    "CONTRACT_RELATIVE_PATH",
    "SCHEMA_VERSION",
    "AccountConnection",
    "AccountConnectionsConfigError",
    "load_account_connections",
    "load_module_account_connections",
]
