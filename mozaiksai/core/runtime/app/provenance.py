"""Optional app provenance contract for Mozaiks app bundles."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mozaiksai.core.runtime.app.paths import APP_PROVENANCE_PATH
from mozaiksai.version import __version__

PROVENANCE_SCHEMA_VERSION = "mozaiks.provenance.v1"

AppProvenanceKind = Literal["generated", "imported", "hand_authored", "app_zero"]
AppProvenanceMode = Literal["factory", "cli", "import", "manual", "refinement"]

DEFAULT_APP_CONTRACTS: dict[str, str] = {
    "app": "mozaiks.app.v1",
    "asset_manifest": "mozaiks.asset_manifest.v1",
    "auth": "mozaiks.auth.v1",
    "dashboard": "mozaiks.dashboard.v1",
    "data": "mozaiks.data_contract.v1",
    "module": "mozaiks.module.v1",
    "provenance": PROVENANCE_SCHEMA_VERSION,
    "refinement_harness": "mozaiks.refinement_harness.v1",
    "security": "mozaiks.secrets.v1",
    "subscriptions": "mozaiks.subscriptions.v1",
    "workflow": "mozaiks.workflow.v1",
}

_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]*$")


class AppProvenanceLoadError(ValueError):
    """Raised when app/provenance.yaml is present but invalid."""


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    text = value.strip()
    return text or None


def _clean_ref(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    if not _REF_RE.match(text):
        raise ValueError(f"{field_name} contains unsupported characters")
    return text


def _clean_key(value: Any, *, field_name: str) -> str:
    text = _clean_ref(value, field_name=field_name).lower()
    if not _KEY_RE.match(text):
        raise ValueError(
            f"{field_name} must start with a lowercase letter and contain only lowercase "
            "letters, numbers, underscores, dots, or hyphens"
        )
    return text


def _clean_relative_path(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string path")
    raw = value.replace("\\", "/").strip()
    if not raw:
        raise ValueError(f"{field_name} must be a non-empty relative path")
    if raw.startswith("/"):
        raise ValueError(f"{field_name} must be relative, not absolute")
    if "://" in raw:
        raise ValueError(f"{field_name} must be an app-bundle path, not a URL")
    if raw.startswith("./"):
        raw = raw[2:]
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"{field_name} must not escape the app bundle")
    if path.parts and ":" in path.parts[0]:
        raise ValueError(f"{field_name} must not include a drive or scheme")
    return str(path)


def _clean_unique_refs(value: Any, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        ref = _clean_ref(item, field_name=f"{field_name}[{index}]")
        if ref not in result:
            result.append(ref)
    return result


class AppProvenanceRunRef(BaseModel):
    """One factory, CLI, import, manual, or refinement touchpoint."""

    model_config = ConfigDict(extra="forbid")

    mode: AppProvenanceMode
    mozaiks_ref: str | None = None
    workflow: str | None = None
    workflow_sequence: str | None = None
    build_id: str | None = None
    artifact_version_id: str | None = None
    app_context_version_id: str | None = None
    timestamp: str | None = None
    notes: str | None = None

    @field_validator(
        "mozaiks_ref",
        "workflow",
        "workflow_sequence",
        "build_id",
        "artifact_version_id",
        "app_context_version_id",
        "timestamp",
        "notes",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        return _clean_optional_text(value)


class AppProvenanceArtifactRefs(BaseModel):
    """Optional durable artifact/version references for factory-built apps."""

    model_config = ConfigDict(extra="forbid")

    generated_artifact_ids: list[str] = Field(default_factory=list)
    accepted_artifact_version_ids: list[str] = Field(default_factory=list)
    app_context_version_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "generated_artifact_ids",
        "accepted_artifact_version_ids",
        "app_context_version_ids",
        mode="before",
    )
    @classmethod
    def _refs(cls, value: Any, info: Any) -> list[str]:
        return _clean_unique_refs(value, field_name=f"artifact_refs.{info.field_name}")


class AppProvenance(BaseModel):
    """Validated contents of ``app/provenance.yaml``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.provenance.v1"] = "mozaiks.provenance.v1"
    app_kind: AppProvenanceKind = "hand_authored"
    created_with: AppProvenanceRunRef
    last_refined_with: AppProvenanceRunRef | None = None
    last_validated_with: AppProvenanceRunRef | None = None
    contracts: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_APP_CONTRACTS))
    overlays: dict[str, str] = Field(default_factory=dict)
    artifact_refs: AppProvenanceArtifactRefs = Field(default_factory=AppProvenanceArtifactRefs)
    notes: list[str] = Field(default_factory=list)

    @field_validator("contracts", mode="before")
    @classmethod
    def _contracts(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("contracts must be a mapping")
        result: dict[str, str] = {}
        for raw_key, raw_ref in value.items():
            key = _clean_key(raw_key, field_name="contract key")
            result[key] = _clean_ref(raw_ref, field_name=f"contracts.{key}")
        return result

    @field_validator("overlays", mode="before")
    @classmethod
    def _overlays(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("overlays must be a mapping")
        result: dict[str, str] = {}
        for raw_key, raw_path in value.items():
            key = _clean_key(raw_key, field_name="overlay key")
            result[key] = _clean_relative_path(raw_path, field_name=f"overlays.{key}")
        return result

    @field_validator("notes", mode="before")
    @classmethod
    def _notes(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("notes must be a list")
        result: list[str] = []
        for index, item in enumerate(value):
            text = _clean_optional_text(item)
            if text is None:
                raise ValueError(f"notes[{index}] must be a non-empty string")
            result.append(text)
        return result


def current_mozaiks_package_ref() -> str:
    """Return a stable package-version provenance reference."""

    return f"package:{__version__}"


def build_default_app_provenance(
    *,
    app_kind: AppProvenanceKind = "hand_authored",
    created_mode: AppProvenanceMode = "cli",
    mozaiks_ref: str | None = None,
    workflow: str | None = None,
    workflow_sequence: str | None = None,
    build_id: str | None = None,
    artifact_version_id: str | None = None,
    app_context_version_id: str | None = None,
    contracts: Mapping[str, str] | None = None,
    overlays: Mapping[str, str] | None = None,
    artifact_refs: Mapping[str, Any] | None = None,
    notes: list[str] | None = None,
) -> AppProvenance:
    """Build the default provenance document for a scaffolded or generated app."""

    merged_contracts = dict(DEFAULT_APP_CONTRACTS)
    if contracts:
        merged_contracts.update(dict(contracts))

    return cast(
        AppProvenance,
        AppProvenance.model_validate(
            {
                "schema_version": PROVENANCE_SCHEMA_VERSION,
                "app_kind": app_kind,
                "created_with": {
                    "mode": created_mode,
                    "mozaiks_ref": mozaiks_ref or current_mozaiks_package_ref(),
                    "workflow": workflow,
                    "workflow_sequence": workflow_sequence,
                    "build_id": build_id,
                    "artifact_version_id": artifact_version_id,
                    "app_context_version_id": app_context_version_id,
                },
                "contracts": merged_contracts,
                "overlays": dict(overlays or {}),
                "artifact_refs": dict(artifact_refs or {}),
                "notes": notes or [],
            }
        ),
    )


def resolve_app_provenance_path(app_root: str | Path) -> Path:
    """Resolve the app-root-relative provenance path."""

    return Path(app_root) / APP_PROVENANCE_PATH


def dump_app_provenance_yaml(provenance: AppProvenance | Mapping[str, Any]) -> str:
    """Serialize app provenance to deterministic YAML."""

    model = (
        provenance
        if isinstance(provenance, AppProvenance)
        else AppProvenance.model_validate(dict(provenance))
    )
    payload = model.model_dump(mode="json", exclude_none=True)
    return cast(str, yaml.safe_dump(payload, allow_unicode=False, sort_keys=False))


def write_app_provenance(
    app_root: str | Path,
    provenance: AppProvenance | Mapping[str, Any],
) -> Path:
    """Write ``provenance.yaml`` under an app root."""

    path = resolve_app_provenance_path(app_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_app_provenance_yaml(provenance), encoding="utf-8")
    return path


def load_app_provenance(app_root: str | Path, *, required: bool = False) -> AppProvenance | None:
    """Load optional ``provenance.yaml`` from an app root."""

    path = resolve_app_provenance_path(app_root)
    if not path.exists():
        if required:
            raise AppProvenanceLoadError(f"{APP_PROVENANCE_PATH} not found")
        return None

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AppProvenanceLoadError(f"Failed to read {APP_PROVENANCE_PATH}: {exc}") from exc

    if not isinstance(raw, dict):
        raise AppProvenanceLoadError(f"{APP_PROVENANCE_PATH} must be a YAML object")

    try:
        return cast(AppProvenance, AppProvenance.model_validate(raw))
    except ValidationError as exc:
        raise AppProvenanceLoadError(f"Invalid {APP_PROVENANCE_PATH}: {exc}") from exc


__all__ = [
    "DEFAULT_APP_CONTRACTS",
    "PROVENANCE_SCHEMA_VERSION",
    "AppProvenance",
    "AppProvenanceArtifactRefs",
    "AppProvenanceKind",
    "AppProvenanceLoadError",
    "AppProvenanceMode",
    "AppProvenanceRunRef",
    "build_default_app_provenance",
    "current_mozaiks_package_ref",
    "dump_app_provenance_yaml",
    "load_app_provenance",
    "resolve_app_provenance_path",
    "write_app_provenance",
]
