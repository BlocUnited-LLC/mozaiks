from __future__ import annotations

"""Names-first app secret resolution from ``app/security/secrets.yaml``."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from mozaiksai.core.runtime.app.paths import APP_SECURITY_SECRETS_PATH
from mozaiksai.core.workflow.paths import resolve_active_app_root

SecretSource = Literal["env", "azure_key_vault", "missing", "invalid"]
_DEFAULT_SECRET_NAME_SUFFIX = "_SECRET_NAME"
_DEFAULT_VAULT_NAME_ENV = "AZURE_KEY_VAULT_NAME"
_DEFAULT_VAULT_URL_ENV = "AZURE_KEY_VAULT_URL"


class _SecretClient(Protocol):
    def get_secret(self, name: str): ...


class SecretResolutionError(Exception):
    """Raised when a secret cannot be resolved without exposing its value."""

    def __init__(self, message: str, *, env_name: str, source: SecretSource = "missing") -> None:
        super().__init__(message)
        self.env_name = env_name
        self.source = source


@dataclass(frozen=True)
class SecretConfig:
    env_name: str
    source: SecretSource
    configured: bool
    secret_ref_env: str
    secret_name: str | None = None
    vault_url_configured: bool = False
    vault_name: str | None = None
    declared: bool = False


def build_secret_client(vault_url: str) -> _SecretClient:
    """Build an Azure Key Vault SecretClient lazily."""

    from azure.identity import DefaultAzureCredential  # type: ignore
    from azure.keyvault.secrets import SecretClient  # type: ignore

    return cast(_SecretClient, SecretClient(vault_url=vault_url, credential=DefaultAzureCredential()))


def _candidate_app_roots(app_root: str | os.PathLike[str] | None = None) -> list[Path]:
    roots: list[Path] = []
    if app_root is not None:
        roots.append(Path(app_root))
    for env_name in ("PLATFORM_PATH", "MOZAIKS_APP_WORKSPACE_PATH"):
        raw = str(os.getenv(env_name) or "").strip()
        if raw:
            roots.append(Path(raw))
    try:
        active = resolve_active_app_root()
        if active.name != "__no_active_app__":
            roots.append(active)
    except Exception:
        pass
    roots.extend([Path.cwd(), Path.cwd() / "app"])

    normalized: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        candidate = root.expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if (candidate / "app.json").exists():
            resolved = candidate.resolve()
        elif (candidate / "app" / "app.json").exists():
            resolved = (candidate / "app").resolve()
        else:
            resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            normalized.append(resolved)
    return normalized


def default_secret_contract_path(app_root: str | os.PathLike[str] | None = None) -> Path:
    for root in _candidate_app_roots(app_root):
        candidate = root / APP_SECURITY_SECRETS_PATH
        if candidate.exists():
            return candidate
    return _candidate_app_roots(app_root)[0] / APP_SECURITY_SECRETS_PATH


def _secret_contract_path(
    *,
    app_root: str | os.PathLike[str] | None = None,
    contract_path: str | os.PathLike[str] | None = None,
) -> Path:
    if contract_path is not None:
        return Path(contract_path).expanduser().resolve()
    configured = os.getenv("MOZAIKS_SECRETS_CONFIG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return default_secret_contract_path(app_root)


def load_secret_contract(
    *,
    app_root: str | os.PathLike[str] | None = None,
    contract_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    path = _secret_contract_path(app_root=app_root, contract_path=contract_path)
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _azure_provider_config(contract: dict[str, Any]) -> dict[str, Any]:
    provider = contract.get("provider")
    if not isinstance(provider, dict):
        return {}
    azure = provider.get("azure_key_vault")
    return azure if isinstance(azure, dict) else {}


def _secret_entry(contract: dict[str, Any], env_name: str) -> dict[str, Any]:
    entries = contract.get("secrets")
    if not isinstance(entries, list):
        return {}
    for entry in entries:
        if isinstance(entry, dict) and str(entry.get("env") or "").strip() == env_name:
            return entry
    return {}


def _secret_name_suffix(contract: dict[str, Any]) -> str:
    azure = _azure_provider_config(contract)
    suffix = str(azure.get("secret_name_env_suffix") or "").strip()
    return suffix or _DEFAULT_SECRET_NAME_SUFFIX


def _secret_ref_env(env_name: str, contract: dict[str, Any] | None = None) -> str:
    return f"{env_name}{_secret_name_suffix(contract or {})}"


def _declared_secret_name(env_name: str, contract: dict[str, Any]) -> str:
    entry = _secret_entry(contract, env_name)
    azure = entry.get("azure_key_vault") if isinstance(entry, dict) else None
    if isinstance(azure, dict):
        secret_name = str(azure.get("secret_name") or "").strip()
        if secret_name:
            return secret_name
    return str(entry.get("secret_name") or "").strip() if isinstance(entry, dict) else ""


def _vault_name(contract: dict[str, Any]) -> str:
    azure = _azure_provider_config(contract)
    name_env = str(azure.get("vault_name_env") or _DEFAULT_VAULT_NAME_ENV).strip()
    env_name = os.getenv(name_env, "").strip() if name_env else ""
    if env_name:
        return env_name
    return str(azure.get("vault_name") or "").strip()


def _vault_url(contract: dict[str, Any]) -> str:
    azure = _azure_provider_config(contract)
    url_env = str(azure.get("vault_url_env") or _DEFAULT_VAULT_URL_ENV).strip()
    env_url = os.getenv(url_env, "").strip() if url_env else ""
    if env_url:
        return env_url
    configured_url = str(azure.get("vault_url") or "").strip()
    if configured_url:
        return configured_url
    vault_name = _vault_name(contract)
    return f"https://{vault_name}.vault.azure.net/" if vault_name else ""


def inspect_secret_config(
    env_name: str,
    *,
    app_root: str | os.PathLike[str] | None = None,
    contract_path: str | os.PathLike[str] | None = None,
) -> SecretConfig:
    contract = load_secret_contract(app_root=app_root, contract_path=contract_path)
    direct = os.getenv(env_name, "").strip()
    ref_env = _secret_ref_env(env_name, contract)
    secret_name = os.getenv(ref_env, "").strip() or _declared_secret_name(env_name, contract)
    vault_url = _vault_url(contract)
    vault_name = _vault_name(contract) or None
    declared = bool(_secret_entry(contract, env_name))

    if direct:
        return SecretConfig(env_name, "env", True, ref_env, None, bool(vault_url), vault_name, declared)
    if secret_name and vault_url:
        return SecretConfig(env_name, "azure_key_vault", True, ref_env, secret_name, True, vault_name, declared)
    if secret_name and not vault_url:
        return SecretConfig(env_name, "invalid", False, ref_env, secret_name, False, vault_name, declared)
    return SecretConfig(env_name, "missing", False, ref_env, None, bool(vault_url), vault_name, declared)


def resolve_secret(
    env_name: str,
    *,
    app_root: str | os.PathLike[str] | None = None,
    contract_path: str | os.PathLike[str] | None = None,
    client_factory: Callable[[str], _SecretClient] | None = None,
) -> str:
    """Resolve an app secret from direct env first, then declared Key Vault refs.

    Secret values are never included in exception strings. Callers should not
    log the returned value.
    """

    contract = load_secret_contract(app_root=app_root, contract_path=contract_path)
    direct = os.getenv(env_name, "").strip()
    if direct:
        return direct

    ref_env = _secret_ref_env(env_name, contract)
    secret_name = os.getenv(ref_env, "").strip() or _declared_secret_name(env_name, contract)
    vault_url = _vault_url(contract)
    if not secret_name:
        raise SecretResolutionError(
            f"{env_name} is not configured. Set {env_name} or {ref_env}.",
            env_name=env_name,
            source="missing",
        )
    if not vault_url:
        raise SecretResolutionError(
            f"{ref_env} or {APP_SECURITY_SECRETS_PATH} declares {env_name}, but Azure Key Vault is missing. "
            "Set AZURE_KEY_VAULT_URL or AZURE_KEY_VAULT_NAME.",
            env_name=env_name,
            source="invalid",
        )

    try:
        client = (client_factory or build_secret_client)(vault_url)
        secret = client.get_secret(secret_name)
        value = str(getattr(secret, "value", "") or "").strip()
    except SecretResolutionError:
        raise
    except Exception as exc:
        raise SecretResolutionError(
            f"{env_name} could not be resolved from Azure Key Vault ({type(exc).__name__}).",
            env_name=env_name,
            source="azure_key_vault",
        ) from exc

    if not value:
        raise SecretResolutionError(
            f"{env_name} resolved from Azure Key Vault but the secret value was empty.",
            env_name=env_name,
            source="azure_key_vault",
        )
    return value


__all__ = [
    "SecretConfig",
    "SecretResolutionError",
    "SecretSource",
    "build_secret_client",
    "default_secret_contract_path",
    "inspect_secret_config",
    "load_secret_contract",
    "resolve_secret",
]
