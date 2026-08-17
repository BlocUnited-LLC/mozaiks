"""Validation facade for ``mozaiks.app_layout.v1`` file-map classification.

The layout registry is the typed authority. This module adds scanner-friendly
classification and diagnostics without changing runtime discovery or
materialization behavior.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.runtime.app.layout_registry import (
    AppLayoutRegistry,
    ArtifactMatch,
    ExtensionSlot,
    LayoutExtension,
    LayoutOwner,
    PathScope,
    Requirement,
    build_app_layout_registry,
)

_REPO_SUPPORT_PREFIXES = (
    ".claude/",
    ".github/",
    "docs/",
    "scripts/",
    "tests/",
)

_REPO_SUPPORT_EXACT = frozenset({".claude", ".github", "docs", "scripts", "tests"})


class LayoutValidationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LayoutClassificationStatus(StrEnum):
    REGISTERED = "registered"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"
    IGNORED_REPO_SUPPORT = "ignored_repo_support"


class LayoutDiagnosticCode(StrEnum):
    PROHIBITED_PATH = "prohibited_path"
    UNKNOWN_PATH = "unknown_path"
    AMBIGUOUS_PATH = "ambiguous_path"
    UNSAFE_PATH = "unsafe_path"


class LayoutFileClassification(LayoutValidationModel):
    path: str
    normalized_path: str
    scope: PathScope | None
    status: LayoutClassificationStatus
    artifact_kind: str | None = None
    owner: str | None = None
    requirement: str | None = None
    assignment_kinds: tuple[str, ...] = Field(default_factory=tuple)
    validator: str | None = None
    runtime_consumer: str | None = None
    security_class: str | None = None
    extension_selection: str | None = None
    values: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None


class LayoutValidationDiagnostic(LayoutValidationModel):
    code: LayoutDiagnosticCode
    path: str
    normalized_path: str
    scope: PathScope | None
    message: str


class LayoutValidationReport(LayoutValidationModel):
    classifications: tuple[LayoutFileClassification, ...]
    diagnostics: tuple[LayoutValidationDiagnostic, ...]

    @property
    def passed(self) -> bool:
        return not self.diagnostics

    @property
    def app_bundle_paths(self) -> tuple[str, ...]:
        return tuple(
            item.normalized_path
            for item in self.classifications
            if item.status == LayoutClassificationStatus.REGISTERED
            and item.scope == PathScope.APP_BUNDLE_ROOT
        )

    @property
    def deployment_paths(self) -> tuple[str, ...]:
        return tuple(
            item.normalized_path
            for item in self.classifications
            if item.status == LayoutClassificationStatus.REGISTERED
            and item.scope == PathScope.DEPLOYMENT_DERIVED
        )


def validate_file_map_layout(
    files_map: Mapping[str, Any],
    *,
    selected_extensions: tuple[LayoutExtension, ...] = (),
    registry: AppLayoutRegistry | None = None,
) -> LayoutValidationReport:
    """Classify generated file-map paths through the app layout registry.

    ``selected_extensions`` is explicit selected-extension authority. Paths do
    not select extensions by filename.
    """

    layout_registry = registry or build_app_layout_registry(selected_extensions)
    classifications: list[LayoutFileClassification] = []
    diagnostics: list[LayoutValidationDiagnostic] = []

    for raw_path in sorted(str(path) for path in files_map):
        classification = classify_layout_path(
            raw_path,
            selected_extensions=selected_extensions,
            registry=layout_registry,
        )
        classifications.append(classification)
        diagnostic = _diagnostic_for(classification)
        if diagnostic is not None:
            diagnostics.append(diagnostic)

    return LayoutValidationReport(
        classifications=tuple(sorted(classifications, key=_classification_sort_key)),
        diagnostics=tuple(sorted(diagnostics, key=_diagnostic_sort_key)),
    )


def classify_layout_path(
    path: str,
    *,
    selected_extensions: tuple[LayoutExtension, ...] = (),
    registry: AppLayoutRegistry | None = None,
) -> LayoutFileClassification:
    layout_registry = registry or build_app_layout_registry(selected_extensions)
    try:
        normalized = _normalize_path(path)
    except ValueError as exc:
        return LayoutFileClassification(
            path=path,
            normalized_path=str(path or ""),
            scope=None,
            status=LayoutClassificationStatus.UNKNOWN,
            reason=str(exc),
        )

    for scope in _candidate_scopes(normalized, registry=layout_registry):
        classification = _match_scope(
            path,
            normalized,
            scope,
            registry=layout_registry,
            selected_extensions=selected_extensions,
        )
        if classification.status in {
            LayoutClassificationStatus.REGISTERED,
            LayoutClassificationStatus.PROHIBITED,
            LayoutClassificationStatus.AMBIGUOUS,
        }:
            return classification

    if _is_repo_support_path(normalized):
        return LayoutFileClassification(
            path=path,
            normalized_path=normalized,
            scope=None,
            status=LayoutClassificationStatus.IGNORED_REPO_SUPPORT,
            reason="repository support file outside generated app bundle",
        )

    return LayoutFileClassification(
        path=path,
        normalized_path=normalized,
        scope=PathScope.APP_BUNDLE_ROOT,
        status=LayoutClassificationStatus.UNKNOWN,
        reason=f"path {normalized!r} is not registered for generated app validation",
    )


def filter_layout_scannable_file_map(
    files_map: Mapping[str, str],
    report: LayoutValidationReport,
) -> dict[str, str]:
    """Return files that remain part of scanner content validation."""

    allowed = {
        item.normalized_path
        for item in report.classifications
        if item.status
        in {
            LayoutClassificationStatus.REGISTERED,
            LayoutClassificationStatus.PROHIBITED,
            LayoutClassificationStatus.UNKNOWN,
        }
        and (
            item.scope in {PathScope.APP_BUNDLE_ROOT, PathScope.DEPLOYMENT_DERIVED}
            or item.scope is None
        )
    }
    result: dict[str, str] = {}
    for raw_path, content in files_map.items():
        try:
            normalized = _normalize_path(raw_path)
        except ValueError:
            continue
        if normalized in allowed:
            result[normalized] = str(content)
    return result


def layout_validation_errors(report: LayoutValidationReport) -> list[str]:
    return [diagnostic.message for diagnostic in report.diagnostics]


def layout_extensions_from_selected_packs(
    capability_packs: list[dict[str, Any]] | None,
) -> tuple[LayoutExtension, ...]:
    """Project explicit selected managed-capability packs to layout extensions."""

    extensions: set[LayoutExtension] = set()
    for pack in capability_packs or []:
        if not isinstance(pack, dict):
            continue
        if str(pack.get("capability_source") or "").strip() != "managed_capability":
            continue
        pack_id = str(pack.get("capability_pack_id") or pack.get("id") or pack.get("pack_id") or "").strip()
        if not pack_id:
            continue
        for slot in (
            ExtensionSlot.MANAGED_CAPABILITY_CLIENT,
            ExtensionSlot.MANAGED_CAPABILITY_CONFIG,
        ):
            try:
                extensions.add(LayoutExtension(slot=slot, pack_id=pack_id))
            except ValueError:
                continue
    return tuple(sorted(extensions, key=lambda item: (item.slot.value, item.pack_id, item.path or "")))


def _match_scope(
    path: str,
    normalized: str,
    scope: PathScope,
    *,
    registry: AppLayoutRegistry,
    selected_extensions: tuple[LayoutExtension, ...],
) -> LayoutFileClassification:
    try:
        match = registry.match_path(normalized, scope)
    except ValueError as exc:
        status = (
            LayoutClassificationStatus.AMBIGUOUS
            if "ambiguous" in str(exc).lower()
            else LayoutClassificationStatus.UNKNOWN
        )
        return LayoutFileClassification(
            path=path,
            normalized_path=normalized,
            scope=scope,
            status=status,
            reason=str(exc),
        )
    return _classification_from_match(
        path,
        normalized,
        scope,
        match,
        selected_extensions=selected_extensions,
    )


def _classification_from_match(
    path: str,
    normalized: str,
    scope: PathScope,
    match: ArtifactMatch,
    *,
    selected_extensions: tuple[LayoutExtension, ...],
) -> LayoutFileClassification:
    family = match.family
    status = (
        LayoutClassificationStatus.PROHIBITED
        if family.requirement == Requirement.PROHIBITED
        else LayoutClassificationStatus.REGISTERED
    )
    return LayoutFileClassification(
        path=path,
        normalized_path=normalized,
        scope=scope,
        status=status,
        artifact_kind=family.kind.value,
        owner=family.owner.value,
        requirement=family.requirement.value,
        assignment_kinds=tuple(kind.value for kind in family.assignment_kinds),
        validator=family.validator.value,
        runtime_consumer=family.runtime_consumer.value,
        security_class=family.security_class.value,
        extension_selection=_extension_selection(match, selected_extensions),
        values={key.value: value for key, value in match.values.items()},
        reason=None,
    )


def _candidate_scopes(normalized: str, *, registry: AppLayoutRegistry) -> tuple[PathScope, ...]:
    if normalized.startswith("generated/"):
        return (PathScope.GENERATED_STAGING,)
    scopes: list[PathScope] = []
    if _matches_scope(registry, normalized, PathScope.DEPLOYMENT_DERIVED):
        scopes.append(PathScope.DEPLOYMENT_DERIVED)
    if _is_repo_support_path(normalized) and _matches_scope(
        registry,
        normalized,
        PathScope.WORKSPACE_ROOT,
    ):
        scopes.append(PathScope.WORKSPACE_ROOT)
    scopes.append(PathScope.APP_BUNDLE_ROOT)
    return tuple(scopes)


def _matches_scope(registry: AppLayoutRegistry, path: str, scope: PathScope) -> bool:
    try:
        registry.match_path(path, scope)
    except ValueError:
        return False
    return True


def _diagnostic_for(classification: LayoutFileClassification) -> LayoutValidationDiagnostic | None:
    if classification.status == LayoutClassificationStatus.PROHIBITED:
        return LayoutValidationDiagnostic(
            code=LayoutDiagnosticCode.PROHIBITED_PATH,
            path=classification.path,
            normalized_path=classification.normalized_path,
            scope=classification.scope,
            message=(
                f"{classification.normalized_path}: removed app paths are explicitly "
                "prohibited by mozaiks.app_layout.v1."
            ),
        )
    if classification.status == LayoutClassificationStatus.AMBIGUOUS:
        return LayoutValidationDiagnostic(
            code=LayoutDiagnosticCode.AMBIGUOUS_PATH,
            path=classification.path,
            normalized_path=classification.normalized_path,
            scope=classification.scope,
            message=(
                f"{classification.normalized_path}: outside the canonical app planes "
                f"or registered generated/deployment scopes: {classification.reason}"
            ),
        )
    if classification.status == LayoutClassificationStatus.UNKNOWN:
        code = (
            LayoutDiagnosticCode.UNSAFE_PATH
            if classification.scope is None
            else LayoutDiagnosticCode.UNKNOWN_PATH
        )
        return LayoutValidationDiagnostic(
            code=code,
            path=classification.path,
            normalized_path=classification.normalized_path,
            scope=classification.scope,
            message=(
                f"{classification.normalized_path}: outside the canonical app planes "
                f"or registered generated/deployment scopes: {classification.reason}"
            ),
        )
    return None


def _extension_selection(
    match: ArtifactMatch,
    selected_extensions: tuple[LayoutExtension, ...],
) -> str | None:
    if match.family.owner not in {LayoutOwner.REGISTERED_EXTENSION, LayoutOwner.CAPABILITY_PACK}:
        return None
    for extension in selected_extensions:
        probe_registry = build_app_layout_registry((extension,))
        try:
            probe = probe_registry.match_path(match.normalized_path, match.family.path_scope)
        except ValueError:
            continue
        if probe.family.owner in {LayoutOwner.REGISTERED_EXTENSION, LayoutOwner.CAPABILITY_PACK}:
            return f"{extension.slot.value}:{extension.pack_id}"
    return match.family.owner.value


def _normalize_path(path: object) -> str:
    text = unicodedata.normalize("NFC", str(path or "")).replace("\\", "/").strip()
    if not text:
        raise ValueError("path must be non-empty")
    if text.startswith("/") or "://" in text:
        raise ValueError(f"absolute paths are not allowed: {path!r}")
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"unsafe path: {path!r}")
    if pure.parts and ":" in pure.parts[0]:
        raise ValueError(f"absolute drive paths are not allowed: {path!r}")
    if any(char in text for char in "*?["):
        raise ValueError(f"glob characters are not allowed in paths: {path!r}")
    return str(pure)


def _is_repo_support_path(path: str) -> bool:
    return path in _REPO_SUPPORT_EXACT or any(path.startswith(prefix) for prefix in _REPO_SUPPORT_PREFIXES)


def _classification_sort_key(item: LayoutFileClassification) -> tuple[str, str, str]:
    return (item.normalized_path, item.status.value, item.scope.value if item.scope else "")


def _diagnostic_sort_key(item: LayoutValidationDiagnostic) -> tuple[str, str, str]:
    return (item.normalized_path, item.code.value, item.message)


__all__ = [
    "LayoutClassificationStatus",
    "LayoutDiagnosticCode",
    "LayoutFileClassification",
    "LayoutValidationDiagnostic",
    "LayoutValidationReport",
    "classify_layout_path",
    "filter_layout_scannable_file_map",
    "layout_extensions_from_selected_packs",
    "layout_validation_errors",
    "validate_file_map_layout",
]
