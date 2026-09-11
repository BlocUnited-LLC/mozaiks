"""The names-only secret contract shared by app generation and runtime."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

EnvName = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]
SecretName = Annotated[str, Field(pattern=r"^[A-Za-z0-9-]+$", max_length=127)]


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AzureVaultPolicy(_ContractModel):
    vault_name: SecretName | None = None
    vault_url: str | None = None
    vault_name_env: EnvName = "AZURE_KEY_VAULT_NAME"
    vault_url_env: EnvName = "AZURE_KEY_VAULT_URL"
    secret_name_env_suffix: Annotated[str, Field(pattern=r"^_[A-Z0-9_]+$")] = "_SECRET_NAME"

    @field_validator("vault_url")
    @classmethod
    def names_only_url(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in ("", "/")):
                raise ValueError("vault_url must be an HTTPS vault origin without credentials")
        return value


class SecretProvider(_ContractModel):
    type: Literal["env", "azure_key_vault"]
    azure_key_vault: AzureVaultPolicy | None = None

    @model_validator(mode="after")
    def provider_policy(self) -> Self:
        if self.type == "env" and self.azure_key_vault is not None:
            raise ValueError("env provider cannot declare an Azure vault policy")
        return self


class AzureSecretReference(_ContractModel):
    secret_name: SecretName


class AppSecretReference(_ContractModel):
    env: EnvName
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")] | None = None
    sensitivity: Literal["secret", "confidential_config"] = "secret"
    required: bool = Field(
        default=False,
        description=(
            "Documents a deployment requirement; it does not eagerly resolve the secret. "
            "The consuming feature enforces availability when selected."
        ),
    )
    purpose: str | None = None
    azure_key_vault: AzureSecretReference | None = None


class AppSecretContract(_ContractModel):
    version: Literal[1]
    kind: Literal["app_secret_contract"] = "app_secret_contract"
    description: str | None = None
    provider: SecretProvider | None = None
    secrets: list[AppSecretReference]

    @model_validator(mode="after")
    def unique_references(self) -> Self:
        names = [entry.env for entry in self.secrets]
        ids = [entry.id for entry in self.secrets if entry.id is not None]
        if len(names) != len(set(names)) or len(ids) != len(set(ids)):
            raise ValueError("secret env names and optional ids must be unique")
        if self.provider and self.provider.type == "env":
            if any(entry.azure_key_vault for entry in self.secrets):
                raise ValueError("env provider cannot declare Azure secret references")
        return self


class SecretContractError(ValueError):
    """Invalid declaration; diagnostics never include submitted values."""


def validate_secret_contract(value: object) -> AppSecretContract:
    try:
        return AppSecretContract.model_validate(value)
    except ValidationError as exc:
        # Pydantic's default error text includes inputs, potentially raw secrets.
        problems = sorted({error["type"] for error in exc.errors(include_input=False)})
        raise SecretContractError("Invalid names-only secret contract: " + ", ".join(problems)) from None
