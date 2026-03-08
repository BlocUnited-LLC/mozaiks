"""Secrets management port protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretsPort(Protocol):
    """Store and resolve secrets by execution scope."""

    async def set_secret(self, *, scope: str, key: str, value: str) -> None:
        ...

    async def get_secret(self, *, scope: str, key: str) -> str | None:
        ...

    async def get_secrets(self, *, scope: str) -> dict[str, str]:
        ...


__all__ = ["SecretsPort"]
