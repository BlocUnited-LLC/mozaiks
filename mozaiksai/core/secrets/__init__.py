"""Secret backend helpers for framework-owned runtime services."""

from .app_secrets import (
    SecretConfig,
    SecretResolutionError,
    SecretSource,
    default_secret_contract_path,
    inspect_secret_config,
    load_secret_contract,
    resolve_secret,
)
from .connector_vault import (
    AzureKeyVaultConnectorVaultBackend,
    ConnectorVaultBackend,
    NoopConnectorVaultBackend,
    describe_connector_vault_backend,
    get_connector_vault_backend,
    reset_connector_vault_backend,
)

__all__ = [
    "AzureKeyVaultConnectorVaultBackend",
    "ConnectorVaultBackend",
    "NoopConnectorVaultBackend",
    "SecretConfig",
    "SecretResolutionError",
    "SecretSource",
    "default_secret_contract_path",
    "describe_connector_vault_backend",
    "get_connector_vault_backend",
    "inspect_secret_config",
    "load_secret_contract",
    "reset_connector_vault_backend",
    "resolve_secret",
]
