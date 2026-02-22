"""Typed, side-effect-free environment settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Kernel settings loaded lazily from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        enable_decoding=False,
        extra="ignore",
    )

    DATABASE_URL: str = Field(
        ...,
        description="SQLAlchemy async DB URL.",
    )
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    OIDC_DISCOVERY_URL: str | None = None
    JWKS_URL: str | None = None
    JWT_ISSUER: str | None = None
    JWT_AUDIENCE: str | None = None
    JWT_ALGORITHMS: list[str] = Field(default_factory=lambda: ["RS256"])
    JWT_REQUIRED_CLAIMS: list[str] = Field(default_factory=lambda: ["exp", "iat", "nbf"])
    JWT_CLOCK_SKEW_SECONDS: int = Field(default=60, ge=0, le=600)
    JWT_JWKS_CACHE_TTL_SECONDS: int = Field(default=3600, ge=1)
    JWT_DISCOVERY_CACHE_TTL_SECONDS: int = Field(default=3600, ge=1)

    DB_POOL_SIZE: int = Field(default=10, ge=1, le=200)
    DB_POOL_MAX_OVERFLOW: int = Field(default=20, ge=0, le=200)
    DB_POOL_TIMEOUT: int = Field(default=30, ge=1, le=120)
    DB_POOL_RECYCLE: int = Field(default=1800, ge=30)
    DB_ECHO: bool = False

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql+asyncpg://"):
            return value
        raise ValueError("DATABASE_URL must use the postgresql+asyncpg scheme.")

    @field_validator("JWT_ALGORITHMS", mode="before")
    @classmethod
    def parse_algorithms(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("JWT_REQUIRED_CLAIMS", mode="before")
    @classmethod
    def parse_required_claims(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


class SettingsProxy:
    """Lazy proxy that resolves concrete settings on first access."""

    def __getattr__(self, item: str) -> Any:
        return getattr(get_settings(), item)

    def model_dump(self) -> dict[str, Any]:
        return get_settings().model_dump()

    def __repr__(self) -> str:
        return "SettingsProxy(get_settings())"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache validated settings."""

    return Settings()


def clear_settings_cache() -> None:
    """Clear cached settings; mainly used by tests."""

    get_settings.cache_clear()


settings = SettingsProxy()

__all__ = ["Settings", "SettingsProxy", "clear_settings_cache", "get_settings", "settings"]
