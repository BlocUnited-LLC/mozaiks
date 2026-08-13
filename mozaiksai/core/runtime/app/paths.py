"""Canonical app-bundle paths used by runtime, builders, and validators."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath

APP_DATA_CONTRACT_PATH = "data/contract.json"
APP_DATA_MIGRATIONS_DIR = "data/migrations"
APP_DATA_MIGRATIONS_GLOB = "data/migrations/*.json"
APP_SECURITY_SECRETS_PATH = "security/secrets.yaml"
APP_AUTH_CONFIG_PATH = "config/auth.yaml"
APP_REFINEMENT_POLICY_CONFIG_PATH = "config/refinement_policy.yaml"
APP_PROVENANCE_PATH = "provenance.yaml"

CANONICAL_APP_CONFIG_FILES = frozenset(
    {
        "config/ai.json",
        APP_AUTH_CONFIG_PATH,
        "config/asset_manifest.json",
        "config/integrations.json",
        "config/integrations.yaml",
        "config/integrations.yml",
        APP_REFINEMENT_POLICY_CONFIG_PATH,
        "config/shell.json",
        "config/subscriptions.yaml",
        "config/targets.json",
    }
)
CANONICAL_APP_ROOT_FILES = frozenset(
    {
        "Dockerfile",
        ".env.example",
        ".env.production.example",
        ".env.staging.example",
        "__init__.py",
        "app.json",
        APP_PROVENANCE_PATH,
        "deployment.manifest.json",
        "docker-compose.yml",
        "index.html",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "pyproject.toml",
        "requirements.txt",
        "tsconfig.json",
        "vite.config.js",
        "vite.config.ts",
        "yarn.lock",
    }
)
CANONICAL_APP_ROOT_DIRS = frozenset(
    {
        "admin",
        "backend",
        "brand",
        ".github",
        ".mozaiks",  # pack provenance and framework metadata directory
        "config",
        "data",
        "dashboard",
        "modules",
        "refinement_harness",
        "security",
        "services",
        "ui",
    }
)

_CANONICAL_APP_CONFIG_SUFFIXES = (".json", ".yaml", ".yml")
_SENSITIVE_CONFIG_TOKEN_RE = re.compile(
    r"(?:^|[._/-])(?:api[_-]?keys?|credentials?|passwords?|secrets?|tokens?)(?:[._/-]|$)",
    re.IGNORECASE,
)

DISALLOWED_LEGACY_APP_PATHS = frozenset(
    {
        "config/data.json",
        "config/llm.yaml",
        "config/secrets.yaml",
    }
)
DISALLOWED_LEGACY_APP_DIR_PREFIXES = frozenset(
    {
        "control_plane/",
        "config/data_migrations/",
        "services/data/",
        "services/security/",
    }
)


def normalize_app_path(raw_path: object) -> str:
    s = str(raw_path or "").replace("\\", "/").strip().lstrip("/")
    while s.startswith("./") or s.startswith("../"):
        s = s[3:] if s.startswith("../") else s[2:]
    return s


def is_safe_app_path(raw_path: object) -> bool:
    """Return whether a raw path is relative and contained by the app root."""

    path = str(raw_path or "").replace("\\", "/").strip()
    if not path or path.startswith("/") or "://" in path:
        return False
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        return False
    return not (pure.parts and ":" in pure.parts[0])


def unsafe_app_paths(paths: Iterable[object]) -> list[str]:
    return sorted({str(path) for path in paths if not is_safe_app_path(path)})


def is_data_migration_path(path: str) -> bool:
    normalized = normalize_app_path(path)
    parts = PurePosixPath(normalized).parts
    return (
        len(parts) == 3
        and parts[0] == "data"
        and parts[1] == "migrations"
        and parts[2].endswith(".json")
        and len(parts[2]) > len(".json")
    )


def is_disallowed_legacy_app_path(path: str) -> bool:
    normalized = normalize_app_path(path)
    if normalized in DISALLOWED_LEGACY_APP_PATHS:
        return True
    return any(normalized.startswith(prefix) for prefix in DISALLOWED_LEGACY_APP_DIR_PREFIXES)


def is_sensitive_app_config_path(path: str) -> bool:
    normalized = normalize_app_path(path)
    if not normalized.startswith("config/"):
        return False
    return bool(_SENSITIVE_CONFIG_TOKEN_RE.search(normalized))


def is_canonical_app_config_path(path: str) -> bool:
    normalized = normalize_app_path(path)
    if not normalized.startswith("config/"):
        return True
    if is_sensitive_app_config_path(normalized):
        return False
    if normalized in CANONICAL_APP_CONFIG_FILES:
        return True
    if normalized.startswith("config/integrations/") and normalized.endswith(_CANONICAL_APP_CONFIG_SUFFIXES):
        return True
    return False


def disallowed_legacy_app_paths(paths: Iterable[str]) -> list[str]:
    return sorted({normalize_app_path(path) for path in paths if is_disallowed_legacy_app_path(path)})


def noncanonical_app_config_paths(paths: Iterable[str]) -> list[str]:
    return sorted(
        {
            normalize_app_path(path)
            for path in paths
            if normalize_app_path(path).startswith("config/")
            and not is_canonical_app_config_path(str(path))
        }
    )


def noncanonical_app_root_paths(paths: Iterable[str]) -> list[str]:
    invalid: set[str] = set()
    for path in paths:
        normalized = normalize_app_path(path)
        if not normalized:
            continue
        root = PurePosixPath(normalized).parts[0]
        if root in CANONICAL_APP_ROOT_DIRS:
            continue
        if normalized in CANONICAL_APP_ROOT_FILES:
            continue
        invalid.add(normalized)
    return sorted(invalid)


__all__ = [
    "APP_DATA_CONTRACT_PATH",
    "APP_DATA_MIGRATIONS_DIR",
    "APP_DATA_MIGRATIONS_GLOB",
    "APP_AUTH_CONFIG_PATH",
    "APP_PROVENANCE_PATH",
    "APP_REFINEMENT_POLICY_CONFIG_PATH",
    "APP_SECURITY_SECRETS_PATH",
    "CANONICAL_APP_CONFIG_FILES",
    "CANONICAL_APP_ROOT_DIRS",
    "CANONICAL_APP_ROOT_FILES",
    "DISALLOWED_LEGACY_APP_DIR_PREFIXES",
    "DISALLOWED_LEGACY_APP_PATHS",
    "disallowed_legacy_app_paths",
    "is_canonical_app_config_path",
    "is_data_migration_path",
    "is_disallowed_legacy_app_path",
    "is_safe_app_path",
    "is_sensitive_app_config_path",
    "noncanonical_app_config_paths",
    "noncanonical_app_root_paths",
    "normalize_app_path",
    "unsafe_app_paths",
]
