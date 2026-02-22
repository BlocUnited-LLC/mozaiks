from __future__ import annotations

import asyncio

from mozaiks.contracts.ports import SecretsPort


def scope_for_run(run_id: str) -> str:
    normalized = run_id.strip()
    if not normalized:
        raise ValueError("run_id must not be empty")
    return f"run:{normalized}"


def scope_for_preview(preview_id: str) -> str:
    normalized = preview_id.strip()
    if not normalized:
        raise ValueError("preview_id must not be empty")
    return f"preview:{normalized}"


def _normalize_scope(scope: str) -> str:
    normalized = scope.strip()
    if not normalized:
        raise ValueError("scope must not be empty")
    if not (normalized.startswith("run:") or normalized.startswith("preview:")):
        raise ValueError("scope must start with 'run:' or 'preview:'")
    return normalized


def _normalize_key(key: str) -> str:
    normalized = key.strip()
    if not normalized:
        raise ValueError("key must not be empty")
    return normalized


class InMemorySecretsStore(SecretsPort):
    """Volatile secrets store scoped to a run/preview execution context."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}
        self._lock = asyncio.Lock()

    async def set_secret(self, *, scope: str, key: str, value: str) -> None:
        scope_key = _normalize_scope(scope)
        secret_key = _normalize_key(key)
        async with self._lock:
            bucket = self._store.setdefault(scope_key, {})
            bucket[secret_key] = value

    async def get_secret(self, *, scope: str, key: str) -> str | None:
        scope_key = _normalize_scope(scope)
        secret_key = _normalize_key(key)
        async with self._lock:
            value = self._store.get(scope_key, {}).get(secret_key)
            return value

    async def get_secrets(self, *, scope: str) -> dict[str, str]:
        scope_key = _normalize_scope(scope)
        async with self._lock:
            return dict(self._store.get(scope_key, {}))

